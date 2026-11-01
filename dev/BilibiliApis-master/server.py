"""BilibiliApis RESTful API 后端服务 (供 Tauri 前端或桌面客户端调用).

提供接口：
1. 登录与 Cookie 管理：
   - GET  /api/auth/status            获取当前登录态与用户信息
   - POST /api/auth/import_cookie     手动导入并持久化 Cookie
   - POST /api/auth/qrcode/generate   生成登录二维码（输出 Base64 图片与 qrcode_key）
   - POST /api/auth/qrcode/poll       轮询扫码状态（成功自动落盘 session.json）
   - POST /api/auth/logout            清除登录会话
2. 关注与分组：
   - GET  /api/relation/tags          获取所有关注分组列表
   - GET  /api/relation/followings    获取指定分组（如 tagid=-10）或全量关注 UP 主
3. 视频聚合索引：
   - GET  /api/videos/index           检索博主全部视频并返回时间倒序的聚合 JSON
4. 音视频下载：
   - POST /api/download/single        下载单个视频或纯音频
   - POST /api/download/batch         批量下载任务
   - GET  /api/download/tasks         查看下载进度
"""

import base64
import io
import json
import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import threading
import time
from pathlib import Path
from typing import List, Optional

import qrcode
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from builder.auth import BiliAuth
from apis.bili_apis import BiliApi
from apis.bili_login_apis import BiliLoginApi
from apis.bili_interact_apis import BiliInteractApi
import re
import uuid
from downloader import MediaDownloader
from utils.session import has_session, load_session, save_session, set_storage_dir

from contextlib import asynccontextmanager
from fastapi.responses import Response
from utils import db
from download_manager import download_manager
from utils.rate_limiter import limiter, bili_limiter, zhihu_limiter
from utils.paths import default_data_dir, get_app_root
from zhihu_router import zhihu_router, get_zhihu_archive_tasks, retry_zhihu_archive_tasks, clear_zhihu_archive_tasks, run_zhihu_auto_check_once
from zhihu_core import zhihu_auth
from utils.logger import app_logger, LOG_FILE_PATH

CORE_INSTANCE_ID = str(uuid.uuid4())
AUTH_TOKEN = os.getenv("BILI_AUTH_TOKEN", "")
DOWNLOAD_DIR = default_data_dir()
qr_login_sessions = {}
last_auto_check_time = 0

# 增量同步并发防重入锁与实时进度状态
_sync_lock = threading.Lock()
_sync_state = {
    "is_syncing": False,
    "scope": "",
    "current": 0,
    "total": 0,
    "message": "空闲"
}

# 追更巡检状态与并发锁
_auto_check_lock = threading.Lock()
_auto_check_state = {
    "is_running": False,
    "last_check_time": 0,
    "last_new_count": 0,
    "total_new_found": 0,
    "last_message": "尚未执行巡检"
}


def _update_sync_state(is_syncing: bool, scope: str = "", current: int = 0, total: int = 0, message: str = ""):
    _sync_state["is_syncing"] = is_syncing
    _sync_state["scope"] = scope
    _sync_state["current"] = current
    _sync_state["total"] = total
    _sync_state["message"] = message


def _detect_video_restriction(item: dict) -> tuple:
    """从 B站 列表或详情字典识别是否为充电专属、付费课程/视频或会员专享受限稿件."""
    if not isinstance(item, dict):
        return 0, ""

    # 1. 充电专属标记 (空间投稿 vlist / 详情 / 动态常见)
    if (
        item.get("is_charging_arc") in (1, True)
        or int(item.get("elec_arc_type") or 0) > 0
        or item.get("is_upower_exclusive") in (1, True)
        or item.get("up_ower_exclusive") in (1, True)
    ):
        return 1, "充电专属"

    # 2. 权益字典 rights (view / fav / 合集常见)
    rights = item.get("rights") or {}
    if isinstance(rights, dict):
        if rights.get("is_upower_exclusive") in (1, True) or rights.get("upower_exclusive") in (1, True):
            return 1, "充电专属"
        if (
            rights.get("pay") == 1
            or rights.get("arc_pay") == 1
            or rights.get("ugcpay") == 1
            or rights.get("pay_free_watch") == 1
        ):
            return 1, "付费视频"
        if rights.get("is_stein_gate") == 1:
            return 1, "互动视频"

    # 3. 顶层付费标记
    if item.get("is_pay") in (1, True) or item.get("ugcpay") in (1, True):
        return 1, "付费视频"

    # 4. 角标文本检测 (如 "充电专属"、"付费"、"会员专享")
    badge_texts = []
    for k in ("badge", "badge_info"):
        val = item.get(k)
        if isinstance(val, str):
            badge_texts.append(val)
        elif isinstance(val, dict):
            badge_texts.append(str(val.get("text") or ""))
    if isinstance(item.get("badges"), list):
        for b in item["badges"]:
            if isinstance(b, dict):
                badge_texts.append(str(b.get("text") or ""))
            elif isinstance(b, str):
                badge_texts.append(b)
    joined_badge = " ".join(badge_texts)
    if "充电" in joined_badge:
        return 1, "充电专属"
    if "付费" in joined_badge or "课程" in joined_badge:
        return 1, "付费视频"
    if "会员" in joined_badge:
        return 1, "会员专享"

    return 0, ""


def _normalize_arc_video(item: dict, mid: int, default_author: str = "") -> dict:
    """将 B 站空间投稿接口原始字典归一化为标准中英双键视频记录，防止入库字段缺失."""
    bvid = item.get("bvid", "")
    created = int(item.get("created") or 0)
    pubdate_str = item.get("pubdate") or (
        time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(created)) if created else ""
    )
    title_str = (item.get("title") or item.get("视频名字") or "").strip()
    author_str = (item.get("author") or item.get("UP名字") or default_author or f"UP_{mid}").strip()
    url_str = item.get("url") or item.get("URL") or (f"https://www.bilibili.com/video/{bvid}" if bvid else "")
    play_cnt = item.get("play", 0)
    length_str = str(item.get("length") or item.get("duration") or item.get("时长") or "")
    pic_str = item.get("pic") or item.get("封面") or ""
    up_mid_val = int(item.get("mid") or item.get("up_mid") or item.get("UP_MID") or mid or 0)
    is_restricted, restrict_reason = _detect_video_restriction(item)

    return {
        "bvid": bvid,
        "aid": item.get("aid"),
        "title": title_str,
        "author": author_str,
        "pubdate": pubdate_str,
        "url": url_str,
        "up_mid": up_mid_val,
        "play": play_cnt,
        "duration": length_str,
        "length": length_str,
        "pic": pic_str,
        "视频名字": title_str,
        "UP名字": author_str,
        "发布日期": pubdate_str,
        "created": created,
        "URL": url_str,
        "UP_MID": up_mid_val,
        "播放量": play_cnt,
        "时长": length_str,
        "封面": pic_str,
        "is_restricted": is_restricted,
        "restrict_reason": restrict_reason,
    }


def _normalize_fav_media(m: dict) -> dict:
    """将 B 站收藏夹明细接口原始字典归一化为标准中英双键视频记录."""
    bvid = m.get("bvid") or m.get("bv_id") or ""
    fav_time = int(m.get("fav_time") or 0)
    pubtime = int(m.get("pubtime") or m.get("ctime") or 0)
    pubdate_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(pubtime)) if pubtime else ""
    upper = m.get("upper") or {}
    cnt_info = m.get("cnt_info") or {}
    duration_sec = int(m.get("duration") or 0)
    length_str = f"{duration_sec // 60}:{duration_sec % 60:02d}" if duration_sec else ""
    title_str = (m.get("title") or "").strip()
    author_str = (upper.get("name") or "").strip()
    url_str = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
    up_mid_val = int(upper.get("mid") or 0)
    play_cnt = cnt_info.get("play", 0)
    pic_str = m.get("cover", "")
    is_restricted, restrict_reason = _detect_video_restriction(m)

    return {
        "bvid": bvid,
        "aid": m.get("id"),
        "title": title_str,
        "author": author_str,
        "pubdate": pubdate_str,
        "url": url_str,
        "up_mid": up_mid_val,
        "play": play_cnt,
        "duration": length_str,
        "length": length_str,
        "pic": pic_str,
        "视频名字": title_str,
        "UP名字": author_str,
        "发布日期": pubdate_str,
        "created": pubtime,
        "fav_time": fav_time,
        "URL": url_str,
        "UP_MID": up_mid_val,
        "播放量": play_cnt,
        "时长": length_str,
        "封面": pic_str,
        "is_restricted": is_restricted,
        "restrict_reason": restrict_reason,
    }


def _start_background_reconciler():
    """后台低频守护线程：每 30 秒自动对账磁盘文件与数据库记录，自愈用户在资源管理器中的删除与移动操作."""
    def _run():
        time.sleep(1)
        while True:
            try:
                db.reconcile_downloaded_files()
            except Exception:
                pass
            time.sleep(30)
    t = threading.Thread(target=_run, daemon=True)
    t.start()


def _run_auto_check_once(trigger: str = "timer") -> dict:
    """执行一次完整的追更巡检（支持定时触发与前端手动立即触发）."""
    global last_auto_check_time
    if not _auto_check_lock.acquire(blocking=False):
        return {
            "success": False,
            "message": "巡检任务正在运行中，请稍候",
            "state": dict(_auto_check_state)
        }

    try:
        _auto_check_state["is_running"] = True
        _auto_check_state["last_message"] = "正在执行追更巡检..."
        last_auto_check_time = time.time()

        auth = get_current_auth(auto_refresh=False)
        scope = db.get_setting("auto_check_scope", "special")
        auto_dl = db.get_setting("auto_download_new", "false").lower() == "true"
        print(f"[*] [追更巡检] 启动检测 (触发方式: {trigger}, 范围: {scope}, 自动下载: {auto_dl})...", flush=True)

        new_videos_found = []
        baseline_initialized_ups = 0

        # 1. 巡检 Bilibili (若已登录且当前账号开启了自动刷新)
        cur_bili_acc = db.get_current_account()
        bili_acc_auto = True
        if cur_bili_acc is not None:
            bili_acc_auto = (cur_bili_acc.get("is_enabled", 1) != 0) and (cur_bili_acc.get("auto_refresh", 1) != 0)
        if auth.is_login and bili_acc_auto:
            tagid = -10 if scope == "special" else 0
            all_followings = (scope == "all" or scope == "all_fav")
            if scope == "special":
                tags_res = BiliApi.get_relation_tags(auth)
                for t_item in (tags_res.get("data") or []):
                    if "特别关注" in t_item.get("name", ""):
                        tagid = t_item.get("tagid", -10)
                        break

            ups = db.get_authors_by_tag(tagid, all_followings=all_followings)

            # 巡检关注博主 (遵循 UP主 与 合集 两级监听开关)
            for up in ups:
                mid = up.get("mid")
                uname = up.get("uname", f"UP_{mid}")
                if not mid:
                    continue
                up_mon = int(up.get("is_monitored", 1))
                collections = db.get_bili_collections(mid)
                monitored_cols = [c for c in collections if c.get("is_monitored") == 1]

                # 若博主关闭了全局监听，且没有开启任何特定合集监听，则跳过
                if up_mon == 0 and not monitored_cols:
                    continue

                # 若博主关闭了全局监听，但开启了特定合集监听，则仅巡检那些被开启监听的合集
                if up_mon == 0 and monitored_cols:
                    for col in monitored_cols:
                        c_type = col.get("collection_type")
                        c_id = col.get("collection_id")
                        try:
                            col_req = ParseCollectionRequest(
                                mid=mid,
                                series_id=c_id if c_type == "series" else None,
                                season_id=c_id if c_type == "season" else None,
                                page=1,
                                page_size=20
                            )
                            col_res = parse_collection(col_req)
                            for ep in (col_res.get("items") or []):
                                bvid = ep.get("bvid")
                                if bvid and not db.get_video_detail(bvid):
                                    norm_item = {
                                        "bvid": bvid,
                                        "aid": ep.get("aid"),
                                        "title": ep.get("title"),
                                        "author": uname,
                                        "up_mid": mid,
                                        "created": int(time.time()),
                                        "pubdate": ep.get("pubdate"),
                                        "url": f"https://www.bilibili.com/video/{bvid}",
                                        "play": 0,
                                        "length": ep.get("duration"),
                                        "pic": ep.get("pic"),
                                        "download_status": "none",
                                        "local_file": "",
                                        "is_read": 0,
                                    }
                                    db.upsert_videos([norm_item], is_read_default=0)
                                    new_videos_found.append(norm_item)
                        except Exception as e:
                            print(f"[!] 巡检合集({c_id}) 失败: {e}", flush=True)
                    continue

                latest_created, _ = db.get_author_sync_meta(mid)
                page_res = BiliApi.get_user_videos(
                    auth=auth,
                    mid=mid,
                    page=1,
                    page_size=20,
                    order="pubdate"
                )
                if page_res.get("code") != 0:
                    continue
                v_list = ((page_res.get("data") or {}).get("list") or {}).get("vlist") or []
                if not v_list:
                    continue

                normalized_items = []
                for v in v_list:
                    if int(v.get("created") or 0) > latest_created:
                        normalized_items.append(_normalize_arc_video(v, mid=mid, default_author=uname))
                    else:
                        break

                if normalized_items:
                    is_read_flag = 0 if latest_created > 0 else 1
                    db.upsert_videos(normalized_items, is_read_default=is_read_flag)
                    max_c = max(item.get("created", 0) for item in normalized_items)
                    db.update_author_sync_meta(mid, max_c)
                    if latest_created > 0:
                        new_videos_found.extend(normalized_items)
                    else:
                        baseline_initialized_ups += 1

            # 若监控范围为 all_fav，同步巡检收藏夹增量视频
            if scope == "all_fav":
                folders = db.get_fav_folders()
                for folder in folders:
                    media_id = folder.get("id")
                    if not media_id:
                        continue
                    latest_known_fav, _ = db.get_fav_folder_sync_meta(media_id)
                    res = BiliInteractApi.get_fav_resources(auth, media_id=media_id, page=1, page_size=20)
                    if res.get("code") != 0:
                        continue
                    medias = (res.get("data") or {}).get("medias") or []
                    fav_new = []
                    for m in medias:
                        fav_time = int(m.get("fav_time") or 0)
                        if fav_time > latest_known_fav:
                            norm_m = _normalize_fav_media(m)
                            if norm_m.get("bvid"):
                                fav_new.append(norm_m)
                        else:
                            break
                    if fav_new:
                        db.save_fav_videos(media_id, fav_new)
                        max_fav_t = max(x.get("fav_time", 0) for x in fav_new)
                        db.update_fav_folder_sync_meta(media_id, max_fav_t)
                        if latest_known_fav > 0:
                            new_videos_found.extend(fav_new)
        else:
            print("[*] [追更巡检] B站账号未登录，跳过B站巡检", flush=True)

        # 2. 巡检知乎增量内容
        zhihu_new_count = 0
        zhihu_auto_enabled = db.get_setting("zhihu_auto_check_enabled", "true").lower() == "true"
        cur_zhihu_acc = db.get_current_zhihu_account()
        if cur_zhihu_acc is None:
            zhihu_auto_enabled = False
        else:
            if cur_zhihu_acc.get("is_enabled", 1) == 0 or cur_zhihu_acc.get("auto_refresh", 1) == 0:
                zhihu_auto_enabled = False
        if zhihu_auto_enabled:
            try:
                zhihu_res = run_zhihu_auto_check_once(auto_archive=auto_dl, scheduled=True)
                zhihu_new_count = zhihu_res.get("new_count", 0)
            except Exception as ze:
                print(f"[!] [知乎巡检] 失败: {ze}", flush=True)

        total_new = len(new_videos_found) + zhihu_new_count
        _auto_check_state["last_check_time"] = int(time.time())
        _auto_check_state["last_new_count"] = total_new
        _auto_check_state["total_new_found"] += total_new

        msg_parts = []
        if auth.is_login:
            msg_parts.append(f"B站 {len(new_videos_found)} 条视频")
        else:
            msg_parts.append("B站未登录")
        if zhihu_auto_enabled:
            msg_parts.append(f"知乎 {zhihu_new_count} 篇内容")

        if total_new > 0:
            msg = f"巡检完成：发现新增 ({', '.join(msg_parts)})"
            if auto_dl and new_videos_found:
                def_v_q = db.get_setting("default_video_quality", "1080P")
                def_a_q = db.get_setting("default_audio_quality", "30280")
                download_manager.add_batch_tasks(auth, new_videos_found, quality=def_v_q, audio_only=False, audio_quality=def_a_q)
                msg += "（视频已推入下载）"
            print(f"[*] [追更巡检] {msg}", flush=True)
        else:
            msg = f"巡检完成：暂无新增内容 ({', '.join(msg_parts)})" + (f"（已为 {baseline_initialized_ups} 位UP主建立基线）" if baseline_initialized_ups else "")
            print(f"[*] [追更巡检] {msg}", flush=True)

        _auto_check_state["last_message"] = msg
        return {
            "success": True,
            "new_count": total_new,
            "bili_new_count": len(new_videos_found),
            "zhihu_new_count": zhihu_new_count,
            "baseline_initialized": baseline_initialized_ups,
            "message": msg,
            "state": dict(_auto_check_state)
        }
    except Exception as e:
        err_msg = f"巡检异常: {e}"
        _auto_check_state["last_message"] = err_msg
        print(f"[!] [追更巡检] {err_msg}", flush=True)
        return {"success": False, "message": err_msg, "state": dict(_auto_check_state)}
    finally:
        _auto_check_state["is_running"] = False
        _auto_check_lock.release()


def _start_auto_checker():
    """后台低频守护线程：按用户设置的间隔定期检查新增视频并执行提醒或自动下载."""
    def _run():
        time.sleep(10)  # 等待应用启动就绪
        while True:
            try:
                enabled = db.get_setting("auto_check_enabled", "false").lower() == "true"
                if enabled:
                    interval_mins = int(db.get_setting("auto_check_interval", "30"))
                    now = time.time()
                    if now - last_auto_check_time >= interval_mins * 60:
                        _run_auto_check_once(trigger="timer")
            except Exception as e:
                print(f"[!] [追更巡检] 守护循环异常: {e}", flush=True)
            time.sleep(30)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def _async_startup_account_sync():
    """后台异步校验初始登录态，避免网络延迟阻塞 FastAPI lifespan 启动."""
    if not has_session():
        return
    try:
        auth = BiliAuth.from_session(auto_refresh=False)
        if auth.is_login:
            nav = BiliApi.get_nav(auth)
            if nav.get("code") == 0:
                data = nav.get("data") or {}
                mid = str(data.get("mid", ""))
                uname = data.get("uname", "")
                face = data.get("face", "")
                vip_type = data.get("vipType", 0)
                db.save_account(
                    mid=mid,
                    uname=uname,
                    face=face,
                    cookie=auth.cookies_str,
                    refresh_token=getattr(auth, 'refresh_token', '') or '',
                    vip_type=vip_type,
                    is_current=1
                )
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    zhihu_auth._load_cookies()
    _start_background_reconciler()
    _start_auto_checker()
    threading.Thread(target=_async_startup_account_sync, daemon=True).start()

    # 载入并应用保存的下载并发线程数
    saved_workers = db.get_setting("download_max_workers", "3")
    try:
        download_manager.set_max_workers(int(saved_workers))
    except Exception:
        pass

    # 载入持久化的限流配置 (B站与知乎)
    saved_limiter_cfg = db.get_setting("rate_limit_config", "")
    if saved_limiter_cfg:
        try:
            cfg = json.loads(saved_limiter_cfg)
            bili_limiter.update_config(**cfg)
        except Exception:
            pass
    saved_zhihu_cfg = db.get_setting("zhihu_rate_limit_config", "")
    if saved_zhihu_cfg:
        try:
            zcfg = json.loads(saved_zhihu_cfg)
            zhihu_limiter.update_config(**zcfg)
        except Exception:
            pass
    print("=" * 64)
    print("  * BiliFollow Local API Core Ready *")
    print(f"  * Backend Address: http://127.0.0.1:{getattr(app.state, 'port', 18888)}")
    print(f"  * Media Storage: {db.get_download_dir()}")
    print(f"  * SQLite Database: {db.DB_FILE}")
    print(f"  * Max Download Workers: {download_manager.max_workers}")
    print(f"  * Rate Limiter: Bili={bili_limiter.rate} QPS | Zhihu={zhihu_limiter.rate} QPS")
    session_status = "Active Account Session Loaded" if has_session() else "Not Logged In"
    print(f"  * Account Status: {session_status}")
    print(f"  * Local Log File: {LOG_FILE_PATH}")
    print("=" * 64)
    app_logger.info(
        "[Core] BiliFollow & Zhihu API Core Ready | DB=%s | Log=%s",
        db.DB_FILE,
        LOG_FILE_PATH,
    )
    print("[*] Waiting for client communication...\n", flush=True)
    yield

app = FastAPI(title="BiliBili Desktop Backend API", version="1.0.0", lifespan=lifespan)

# 启用跨域允许前端访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(zhihu_router)


@app.middleware("http")
async def auth_token_middleware(request, call_next):
    if AUTH_TOKEN:
        path = request.url.path
        if request.method != "OPTIONS" and not path.startswith("/api/health") and not path.startswith("/api/proxy/image"):
            req_token = request.headers.get("X-Auth-Token") or request.query_params.get("token")
            if req_token != AUTH_TOKEN:
                return Response(
                    content=json.dumps({"detail": "Unauthorized: Invalid or missing X-Auth-Token"}),
                    status_code=401,
                    media_type="application/json",
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Headers": "*",
                        "Access-Control-Allow-Methods": "*",
                    },
                )
    return await call_next(request)


@app.middleware("http")
async def log_requests(request, call_next):
    start_time = time.time()
    method = request.method
    path = request.url.path
    is_silent = (
        method == "OPTIONS"
        or path in ("/api/download/tasks", "/api/limiter/status", "/api/health")
        or path.startswith("/api/proxy/image")
    )
    query_str = f"?{request.url.query}" if request.url.query else ""
    if not is_silent:
        app_logger.info("[HTTP REQ]  --> %s %s%s", method, path, query_str)
    response = await call_next(request)
    duration = (time.time() - start_time) * 1000
    if not is_silent:
        body_bytes = b""
        try:
            async for chunk in response.body_iterator:
                body_bytes += chunk
            resp_preview = body_bytes.decode("utf-8", errors="replace")
            # 脱敏 base64 二维码图片内容，避免刷屏
            if "data:image/png;base64," in resp_preview:
                resp_preview = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "data:image/png;base64,<QR_BASE64>", resp_preview)
            if len(resp_preview) > 700:
                resp_preview = resp_preview[:700] + f"... [truncated, total={len(body_bytes)}B]"
            app_logger.info(
                "[HTTP RESP] <-- %s %s | 状态: %s | 耗时: %.1fms | 响应: %s",
                method,
                path,
                response.status_code,
                duration,
                resp_preview,
            )
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
        except Exception:
            app_logger.info("[HTTP RESP] <-- %s %s | 状态: %s | 耗时: %.1fms", method, path, response.status_code, duration)
    return response


@app.get("/api/system/logs")
def get_system_logs(lines: int = 120):
    """读取本地日志文件尾部内容，方便前端或调试器直接检视."""
    try:
        if os.path.exists(LOG_FILE_PATH):
            with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            tail = [ln.rstrip("\r\n") for ln in all_lines[-max(10, min(lines, 500)):]]
            return {"log_file": str(LOG_FILE_PATH), "lines": tail}
    except Exception as e:
        return {"log_file": str(LOG_FILE_PATH), "lines": [f"读取日志失败: {e}"]}
    return {"log_file": str(LOG_FILE_PATH), "lines": []}


@app.get("/api/health")
def health_check():
    """核心服务健康检查握手."""
    return {
        "status": "ok",
        "version": "1.0.0",
        "coreInstanceId": CORE_INSTANCE_ID,
        "pid": os.getpid(),
        "db": "ok"
    }




def _parse_semver(v: str) -> tuple:
    import re
    cleaned = re.sub(r'^[vV]', '', str(v).strip())
    parts = []
    for piece in re.split(r'[-+]', cleaned)[0].split('.'):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])

_cached_update_info = None
_cached_update_time = 0
_update_download_lock = threading.Lock()
_update_download_state = {
    "status": "idle",  # idle | downloading | completed | failed
    "progress": 0,
    "downloaded_bytes": 0,
    "total_bytes": 0,
    "speed_text": "",
    "file_name": "",
    "file_path": "",
    "error": "",
    "updated_at": 0,
}


def _get_platform_arch_info() -> tuple[str, str]:
    import platform as _plat
    os_name = "windows" if sys.platform == "win32" else ("macos" if sys.platform == "darwin" else "linux")
    machine = (_plat.machine() or "").lower()
    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = "x64"
    return os_name, arch


def _select_best_release_asset(assets: list, target_os: Optional[str] = None, target_arch: Optional[str] = None) -> dict:
    """根据当前操作系统 (Windows/macOS) 与芯片架构 (x64/arm64) 从 GitHub Release assets 中自动挑选最匹配的安装包."""
    if not assets or not isinstance(assets, list):
        return {}
    os_name, arch = _get_platform_arch_info()
    if target_os:
        os_name = target_os
    if target_arch:
        arch = target_arch

    best_asset = None
    best_score = -9999

    for a in assets:
        if not isinstance(a, dict):
            continue
        url = str(a.get("browser_download_url") or "").strip()
        name = str(a.get("name") or "").strip()
        if not url or not name:
            continue
        lname = name.lower()

        # 排除签名、哈希校验与元数据文件
        if lname.endswith((".sig", ".sha256", ".sha512", ".md5", ".txt", ".json", ".yml", ".yaml", ".blockmap")):
            continue

        score = 0
        is_mac_asset = any(k in lname for k in (".dmg", ".pkg", ".app.tar.gz", "macos", "darwin", "osx", "apple"))
        is_win_asset = any(k in lname for k in (".exe", ".msi", "windows", "win64", "win32", "_win", "-win"))
        is_linux_asset = any(k in lname for k in (".deb", ".rpm", ".appimage", "linux"))

        if os_name == "windows":
            if is_mac_asset or is_linux_asset:
                continue
            if lname.endswith(".exe"):
                score += 100
                if "setup" in lname or "install" in lname:
                    score += 30
            elif lname.endswith(".msi"):
                score += 90
            elif lname.endswith((".zip", ".7z")):
                score += 60
                if is_win_asset:
                    score += 20
        elif os_name == "macos":
            if is_win_asset or is_linux_asset:
                continue
            if lname.endswith(".dmg"):
                score += 110
            elif lname.endswith(".pkg"):
                score += 100
            elif lname.endswith(".zip") and is_mac_asset:
                score += 80
            else:
                continue
        else:
            if is_win_asset or is_mac_asset:
                continue
            if is_linux_asset:
                score += 100

        has_arm = any(k in lname for k in ("arm64", "aarch64", "apple-silicon", "m1"))
        has_x64 = any(k in lname for k in ("x64", "x86_64", "amd64", "win64", "intel"))
        has_universal = "universal" in lname

        if arch == "arm64":
            if has_arm:
                score += 40
            elif has_universal:
                score += 25
            elif has_x64:
                score -= 25
        else:
            if has_x64:
                score += 40
            elif has_universal:
                score += 25
            elif has_arm:
                score -= 40

        if score > best_score:
            best_score = score
            best_asset = {
                "name": name,
                "download_url": url,
                "size": int(a.get("size") or 0),
            }

    return best_asset or {}


def _launch_update_installer(file_path: str, reveal_in_folder: bool = False) -> tuple[bool, str]:
    """触发运行下载好的安装包（或打开所在文件夹供用户手动安装）."""
    if not file_path or not os.path.exists(file_path):
        return False, "未找到已下载的更新文件"
    try:
        if os.path.getsize(file_path) <= 0:
            return False, "更新安装包文件为空 (0 字节)，请重新下载"
    except OSError as e:
        return False, f"无法读取更新安装包状态: {e}"
    norm_path = os.path.normpath(file_path)
    lname = norm_path.lower()
    try:
        if sys.platform == "win32":
            if reveal_in_folder or lname.endswith((".zip", ".7z", ".rar")):
                subprocess.Popen(["explorer", f"/select,{norm_path}"])
                return True, "已打开更新包所在文件夹"
            os.startfile(norm_path)
            return True, "已启动安装程序，请按提示完成安装"
        elif sys.platform == "darwin":
            if reveal_in_folder:
                subprocess.Popen(["open", "-R", norm_path])
                return True, "已在 Finder 中定位更新包"
            subprocess.Popen(["open", norm_path])
            return True, "已打开安装镜像，请完成替换安装"
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(norm_path)])
            return True, "已打开更新文件目录"
    except Exception as e:
        return False, f"启动安装程序失败: {e}"


@app.get("/api/system/check_update")
def check_system_update(force: bool = False):
    """检查 GitHub 最新版本更新信息，并自动匹配当前操作系统与芯片架构的安装包."""
    import urllib.request
    global _cached_update_info, _cached_update_time
    now = time.time()
    if not force and _cached_update_info and (now - _cached_update_time < 300):
        return _cached_update_info

    current_ver = "0.2.0"
    version_file = get_app_root() / "version.json"
    if version_file.exists():
        try:
            with open(version_file, "r", encoding="utf-8") as f:
                vdata = json.load(f)
                current_ver = vdata.get("version", current_ver)
        except Exception:
            pass

    os_name, arch = _get_platform_arch_info()
    headers = {
        "User-Agent": "GatherBox-Lite-App/0.2.0",
        "Accept": "application/vnd.github.v3+json"
    }

    latest_ver = None
    release_name = None
    changelog = None
    published_at = None
    download_url = ""
    asset_name = ""
    asset_size = 0
    html_url = "https://github.com/joelovechen/GatherBox_Lite/releases"

    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/joelovechen/GatherBox_Lite/releases/latest",
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                latest_ver = data.get("tag_name", "").lstrip("vV")
                release_name = data.get("name") or f"GatherBox Lite v{latest_ver}"
                changelog = data.get("body", "")
                published_at = data.get("published_at", "")
                html_url = data.get("html_url", html_url)
                assets = data.get("assets", [])
                matched = _select_best_release_asset(assets)
                if matched:
                    download_url = matched.get("download_url", "")
                    asset_name = matched.get("name", "")
                    asset_size = matched.get("size", 0)
    except Exception as e:
        app_logger.warning("GitHub Releases API check failed, fallback to raw version.json: %s", e)

    if not latest_ver:
        urls_to_try = [
            "https://raw.githubusercontent.com/joelovechen/GatherBox_Lite/main/version.json",
            "https://mirror.ghproxy.com/https://raw.githubusercontent.com/joelovechen/GatherBox_Lite/main/version.json",
        ]
        for url in urls_to_try:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "GatherBox-Lite-App/0.2.0"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status == 200:
                        raw_data = json.loads(resp.read().decode("utf-8"))
                        latest_ver = raw_data.get("version")
                        release_name = raw_data.get("name") or f"GatherBox Lite v{latest_ver}"
                        changelog = raw_data.get("changelog", "")
                        html_url = raw_data.get("html_url", html_url)
                        published_at = raw_data.get("release_date", "")
                        if isinstance(raw_data.get("assets"), list):
                            matched = _select_best_release_asset(raw_data["assets"])
                            if matched:
                                download_url = matched.get("download_url", "")
                                asset_name = matched.get("name", "")
                                asset_size = matched.get("size", 0)
                        if not download_url:
                            if os_name == "macos":
                                download_url = (
                                    raw_data.get(f"download_url_macos_{arch}")
                                    or raw_data.get("download_url_macos")
                                    or raw_data.get("download_url", "")
                                )
                            else:
                                download_url = (
                                    raw_data.get(f"download_url_windows_{arch}")
                                    or raw_data.get("download_url_windows")
                                    or raw_data.get("download_url", "")
                                )
                        if download_url and not asset_name:
                            asset_name = download_url.rstrip("/").split("/")[-1]
                        break
            except Exception:
                continue

    if not latest_ver:
        return {
            "success": False,
            "has_update": False,
            "current_version": current_ver,
            "latest_version": current_ver,
            "os_platform": os_name,
            "arch": arch,
            "asset_name": "",
            "asset_size": 0,
            "message": "未能连接至 GitHub 更新服务，请检查网络或稍后重试",
            "download_url": "",
            "html_url": html_url,
            "checked_at": int(now)
        }

    has_update = _parse_semver(latest_ver) > _parse_semver(current_ver)
    res = {
        "success": True,
        "has_update": has_update,
        "current_version": current_ver,
        "latest_version": latest_ver,
        "os_platform": os_name,
        "arch": arch,
        "asset_name": asset_name,
        "asset_size": asset_size,
        "release_name": release_name,
        "changelog": changelog,
        "published_at": published_at,
        "download_url": download_url,
        "html_url": html_url,
        "checked_at": int(now)
    }
    _cached_update_info = res
    _cached_update_time = now
    return res


class UpdateDownloadRequest(BaseModel):
    download_url: Optional[str] = None
    file_name: Optional[str] = None
    auto_launch: bool = True


class UpdateLaunchRequest(BaseModel):
    file_path: Optional[str] = None
    reveal_in_folder: bool = False


@app.post("/api/system/update/download")
def start_update_download(req: UpdateDownloadRequest):
    """在软件内异步下载匹配好的更新安装包（支持 GitHub 直连与国内镜像回退，下载完成后自动拉起安装程序）."""
    import urllib.request
    global _update_download_state

    with _update_download_lock:
        if _update_download_state.get("status") == "downloading":
            return {"success": True, **_update_download_state}

    target_url = (req.download_url or "").strip()
    target_name = (req.file_name or "").strip()
    if not target_url and _cached_update_info:
        target_url = str(_cached_update_info.get("download_url") or "").strip()
        target_name = target_name or str(_cached_update_info.get("asset_name") or "").strip()

    if not target_url or target_url.rstrip("/").endswith("/releases"):
        raise HTTPException(
            status_code=400,
            detail="当前版本尚未提供直接安装包下载链接，请点击「打开发布页」手动下载",
        )

    if not target_name:
        target_name = urllib.parse.unquote(target_url.split("?")[0].rstrip("/").split("/")[-1]) or "GatherBox_Update_Installer.exe"
    target_name = re.sub(r'[<>:"/\\|?*]', "_", target_name)

    updates_dir = Path(db.get_download_dir()) / "_updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    final_path = updates_dir / target_name
    part_path = updates_dir / f"{target_name}.part"

    with _update_download_lock:
        _update_download_state = {
            "status": "downloading",
            "progress": 0,
            "downloaded_bytes": 0,
            "total_bytes": int((_cached_update_info or {}).get("asset_size") or 0),
            "speed_text": "正在连接下载源...",
            "file_name": target_name,
            "file_path": str(final_path),
            "error": "",
            "updated_at": int(time.time()),
        }

    def _download_worker():
        global _update_download_state
        candidate_urls = [target_url]
        if "github.com/" in target_url and not target_url.startswith("https://mirror."):
            candidate_urls.extend([
                f"https://ghp.ci/{target_url}",
                f"https://mirror.ghproxy.com/{target_url}",
            ])

        last_err = ""
        for idx, try_url in enumerate(candidate_urls):
            try:
                if idx > 0:
                    with _update_download_lock:
                        _update_download_state["speed_text"] = f"切换加速源 ({idx}/{len(candidate_urls) - 1})..."
                        _update_download_state["updated_at"] = int(time.time())

                req_obj = urllib.request.Request(
                    try_url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GatherBox-Updater/0.2.0"},
                )
                with urllib.request.urlopen(req_obj, timeout=20) as resp:
                    content_len = int(resp.headers.get("Content-Length") or 0)
                    expected_asset_size = int((_cached_update_info or {}).get("asset_size") or 0)
                    total_bytes = content_len or expected_asset_size
                    downloaded = 0
                    start_ts = time.time()
                    last_tick = start_ts
                    last_bytes = 0

                    with open(part_path, "wb") as f:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            now_ts = time.time()
                            if now_ts - last_tick >= 0.25:
                                elapsed = max(now_ts - last_tick, 0.001)
                                bps = (downloaded - last_bytes) / elapsed
                                if bps >= 1024 * 1024:
                                    spd = f"{bps / (1024 * 1024):.2f} MB/s"
                                else:
                                    spd = f"{max(bps / 1024, 0.1):.1f} KB/s"
                                pct = int(min(99, (downloaded * 100) // total_bytes)) if total_bytes > 0 else 50
                                with _update_download_lock:
                                    _update_download_state["downloaded_bytes"] = downloaded
                                    _update_download_state["total_bytes"] = total_bytes
                                    _update_download_state["progress"] = pct
                                    _update_download_state["speed_text"] = spd
                                    _update_download_state["updated_at"] = int(now_ts)
                                last_tick = now_ts
                                last_bytes = downloaded

                # 基础完整性校验：拒绝空文件，有明确长度时严格核对字节数
                part_size = part_path.stat().st_size if part_path.exists() else 0
                if downloaded <= 0 or part_size <= 0:
                    if part_path.exists():
                        try:
                            part_path.unlink()
                        except OSError:
                            pass
                    raise RuntimeError("下载到的更新文件为空 (0 字节)")
                if content_len > 0 and part_size != content_len:
                    try:
                        part_path.unlink()
                    except OSError:
                        pass
                    raise RuntimeError(f"文件长度校验不一致：响应声明 {content_len} 字节，实际接收 {part_size} 字节")
                if content_len <= 0 and expected_asset_size > 0 and part_size != expected_asset_size:
                    try:
                        part_path.unlink()
                    except OSError:
                        pass
                    raise RuntimeError(f"文件长度与版本信息不一致：预期 {expected_asset_size} 字节，实际接收 {part_size} 字节")

                os.replace(part_path, final_path)

                launch_err = ""
                if req.auto_launch:
                    ok_launch, launch_msg = _launch_update_installer(str(final_path), reveal_in_folder=False)
                    if not ok_launch:
                        launch_err = launch_msg

                with _update_download_lock:
                    _update_download_state["status"] = "completed"
                    _update_download_state["progress"] = 100
                    _update_download_state["downloaded_bytes"] = final_path.stat().st_size
                    _update_download_state["total_bytes"] = final_path.stat().st_size
                    _update_download_state["speed_text"] = "下载完成（安装启动失败）" if launch_err else "下载完成"
                    _update_download_state["file_path"] = str(final_path)
                    _update_download_state["error"] = launch_err
                    _update_download_state["updated_at"] = int(time.time())
                return
            except Exception as e:
                last_err = str(e)
                app_logger.warning("[Updater] 下载源失败 (%s): %s", try_url, e)
                continue

        with _update_download_lock:
            _update_download_state["status"] = "failed"
            _update_download_state["speed_text"] = "下载失败"
            _update_download_state["error"] = f"下载更新包失败: {last_err}"
            _update_download_state["updated_at"] = int(time.time())

    threading.Thread(target=_download_worker, daemon=True).start()
    return {"success": True, **_update_download_state}


@app.get("/api/system/update/download_status")
def get_update_download_status():
    """查询软件内更新包下载进度与状态."""
    with _update_download_lock:
        return dict(_update_download_state)


@app.post("/api/system/update/launch_installer")
def launch_downloaded_installer(req: UpdateLaunchRequest):
    """触发执行已下载的更新安装包（或在文件夹中显示）."""
    target_path = (req.file_path or "").strip()
    if not target_path:
        with _update_download_lock:
            target_path = str(_update_download_state.get("file_path") or "")
    ok, msg = _launch_update_installer(target_path, reveal_in_folder=req.reveal_in_folder)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg, "file_path": target_path}

@app.post("/api/system/shutdown")
def system_shutdown(background_tasks: BackgroundTasks):
    """受控优雅停机端点."""
    def _exit():
        time.sleep(0.5)
        os._exit(0)
    background_tasks.add_task(_exit)
    return {"success": True, "message": "Server shutting down"}


def get_current_auth(auto_refresh: bool = False) -> BiliAuth:
    """获取当前可用的 Auth 实例."""
    if has_session():
        try:
            return BiliAuth.from_session(auto_refresh=auto_refresh)
        except Exception:
            pass
    return BiliAuth.anonymous()


# ---------------------------------------------------------------------- Request Models

class ImportCookieRequest(BaseModel):
    cookie: str

class PollQRRequest(BaseModel):
    qrcode_key: str

class SwitchAccountRequest(BaseModel):
    mid: str

class RemoveAccountRequest(BaseModel):
    mid: str

class UpdateAccountFlagsRequest(BaseModel):
    mid: str
    is_enabled: Optional[bool] = None
    auto_refresh: Optional[bool] = None

class DownloadSingleRequest(BaseModel):
    video: dict
    quality: str = "1080P"
    audio_only: bool = False
    audio_quality: str = "30280"
    block_paid: Optional[bool] = None

class DownloadBatchRequest(BaseModel):
    videos: List[dict]
    quality: str = "1080P"
    audio_only: bool = False
    audio_quality: str = "30280"
    block_paid: Optional[bool] = None


# ---------------------------------------------------------------------- Auth APIs

@app.get("/api/auth/status")
def auth_status():
    """获取当前 Cookie 登录状态与用户信息."""
    auth = get_current_auth(auto_refresh=False)
    if not auth.is_login:
        return {"is_login": False, "mid": "", "uname": "", "message": "未登录或 Cookie 已失效"}

    nav = BiliApi.get_nav(auth)
    if nav.get("code") == 0:
        data = nav.get("data") or {}
        return {
            "is_login": True,
            "mid": data.get("mid"),
            "uname": data.get("uname"),
            "face": data.get("face"),
            "vip_type": data.get("vipType", 0),
            "message": "已登录"
        }
    return {"is_login": False, "mid": "", "uname": "", "message": "Cookie 无效或已过期"}


@app.get("/api/auth/accounts")
def list_accounts():
    """获取所有已保存的多账号列表."""
    accounts = db.get_accounts()
    return {"accounts": accounts}


@app.post("/api/auth/switch_account")
def switch_account(req: SwitchAccountRequest):
    """切换当前激活账号."""
    target_mid = req.mid.strip()
    acc = db.get_account_by_mid(target_mid)
    if not acc:
        raise HTTPException(status_code=404, detail="账号未找到")

    db.set_current_account(target_mid)
    try:
        auth = BiliAuth.from_cookie(acc["cookie"])
        auth.refresh_token = acc.get("refresh_token") or ""
        save_session(auth)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"会话构建失败: {e}")

    nav = BiliApi.get_nav(auth)
    user_info = nav.get("data") or {}
    return {
        "success": True,
        "mid": user_info.get("mid", target_mid),
        "uname": user_info.get("uname", acc.get("uname")),
        "face": user_info.get("face", acc.get("face")),
        "message": f"已切换至账号: {acc.get('uname')}"
    }


@app.post("/api/auth/remove_account")
def remove_account(req: RemoveAccountRequest):
    """移除指定账号."""
    target_mid = req.mid.strip()
    current = db.get_current_account()
    is_current = (current and str(current["mid"]) == str(target_mid))
    db.remove_account(target_mid)

    if is_current:
        remaining = db.get_accounts()
        if remaining:
            next_mid = str(remaining[0]["mid"])
            db.set_current_account(next_mid)
            next_acc = db.get_account_by_mid(next_mid)
            if next_acc:
                try:
                    auth = BiliAuth.from_cookie(next_acc["cookie"])
                    auth.refresh_token = next_acc.get("refresh_token") or ""
                    save_session(auth)
                except Exception:
                    pass
        else:
            from utils.session import clear_session
            clear_session()

    return {"success": True, "message": "账号已成功移除"}


@app.post("/api/auth/update_account_flags")
def update_bili_account_flags(req: UpdateAccountFlagsRequest):
    """更新 B 站账号启用状态与自动刷新开关."""
    target_mid = req.mid.strip()
    if not target_mid:
        raise HTTPException(status_code=400, detail="缺少 mid")
    db.update_account_flags(
        target_mid,
        is_enabled=int(bool(req.is_enabled)) if req.is_enabled is not None else None,
        auto_refresh=int(bool(req.auto_refresh)) if req.auto_refresh is not None else None,
    )
    return {"success": True, "accounts": db.get_accounts()}


@app.post("/api/auth/import_cookie")
def import_cookie(req: ImportCookieRequest):
    """手动导入并保存 Cookie 字符串."""
    raw_cookie = req.cookie.strip()
    if not raw_cookie:
        raise HTTPException(status_code=400, detail="Cookie 不能为空")

    try:
        auth = BiliAuth.from_cookie(raw_cookie)
        nav = BiliApi.get_nav(auth)
        if nav.get("code") != 0:
            raise HTTPException(status_code=400, detail=f"Cookie 校验失败: {nav.get('message')}")

        data = nav.get("data") or {}
        save_session(auth)
        mid = str(data.get("mid", ""))
        uname = data.get("uname", "")
        face = data.get("face", "")
        vip_type = data.get("vipType", 0)
        db.save_account(mid=mid, uname=uname, face=face, cookie=raw_cookie, vip_type=vip_type, is_current=1)

        return {
            "success": True,
            "mid": mid,
            "uname": uname,
            "face": face,
            "message": "Cookie 导入成功并已持久化"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")


@app.post("/api/auth/qrcode/generate")
def generate_qrcode():
    """生成登录二维码，返回 Base64 图片与用于轮询的 qrcode_key."""
    auth = BiliAuth.anonymous()
    res = BiliLoginApi.qrcode_generate(auth)
    if res.get("code") != 0:
        raise HTTPException(status_code=500, detail=f"获取二维码失败: {res.get('message')}")

    data = res.get("data") or {}
    url = data.get("url")
    qrcode_key = data.get("qrcode_key")

    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    qr_login_sessions[qrcode_key] = auth

    return {
        "success": True,
        "qrcode_key": qrcode_key,
        "qrcode_url": url,
        "qrcode_img": img_b64
    }


@app.post("/api/auth/qrcode/poll")
def poll_qrcode(req: PollQRRequest):
    """轮询扫码状态，成功时自动写入 session.json 与 accounts 表."""
    qrcode_key = req.qrcode_key
    auth = qr_login_sessions.get(qrcode_key) or BiliAuth.anonymous()

    poll_json, cookies = BiliLoginApi.qrcode_poll(auth, qrcode_key)
    data = poll_json.get("data") or {}
    code = data.get("code")

    if code == 0:
        auth.update_cookies(cookies)
        auth.refresh_token = data.get("refresh_token") or ""
        save_session(auth)
        qr_login_sessions.pop(qrcode_key, None)

        nav = BiliApi.get_nav(auth)
        user_info = nav.get("data") or {}
        mid = str(user_info.get("mid", ""))
        uname = user_info.get("uname", "")
        face = user_info.get("face", "")
        vip_type = user_info.get("vipType", 0)
        db.save_account(
            mid=mid,
            uname=uname,
            face=face,
            cookie=auth.cookies_str,
            refresh_token=auth.refresh_token,
            vip_type=vip_type,
            is_current=1
        )

        return {
            "status": "success",
            "message": "登录成功",
            "mid": mid,
            "uname": uname,
            "face": face
        }
    elif code == 86090:
        return {"status": "scanned", "message": "已扫码，请在手机端确认"}
    elif code == 86101:
        return {"status": "waiting", "message": "等待扫码"}
    elif code == 86038:
        qr_login_sessions.pop(qrcode_key, None)
        return {"status": "expired", "message": "二维码已过期，请刷新"}
    else:
        return {"status": "error", "message": data.get("message", "未知状态")}


@app.post("/api/auth/logout")
def logout():
    """清除当前账号登录状态."""
    from utils.session import clear_session
    current = db.get_current_account()
    if current:
        db.set_setting("current_account_mid", "")
    clear_session()
    return {"success": True, "message": "已退出登录"}


# ---------------------------------------------------------------------- Relation & Videos

@app.get("/api/relation/tags")
def get_tags():
    """获取当前账号的所有关注分组列表."""
    auth = get_current_auth()
    if not auth.is_login:
        raise HTTPException(status_code=401, detail="请先登录")
    res = BiliApi.get_relation_tags(auth)
    if res.get("code") != 0:
        raise HTTPException(status_code=500, detail=res.get("message"))
    return {"tags": res.get("data") or []}


@app.get("/api/relation/followings")
def get_followings(tagid: int = -10, all_followings: bool = False, local_only: bool = False):
    """获取分组下的博主（例如 tagid=-10 特别关注）或全量关注列表，优先落盘 SQLite."""
    if local_only:
        local_authors = db.get_authors_by_tag(tagid, all_followings)
        if local_authors:
            return {"total": len(local_authors), "items": local_authors}
    auth = get_current_auth()
    if not auth.is_login:
        local_authors = db.get_authors_by_tag(tagid, all_followings)
        return {"total": len(local_authors), "items": local_authors}

    if all_followings:
        mid = auth.mid
        succ, msg, items = BiliApi.get_all_followings(auth, vmid=mid)
    else:
        succ, msg, items = BiliApi.get_all_followings_by_tag(auth, tagid=tagid)

    if not succ:
        print(f"[-] 获取博主列表网络失败 (tagid={tagid}, all={all_followings}): {msg}，尝试回退本地数据库", flush=True)
        local_authors = db.get_authors_by_tag(tagid, all_followings)
        return {"total": len(local_authors), "items": local_authors}

    # 同步落盘博主信息到 SQLite，并合并本地已订阅博主返回
    db.save_authors(items, tagid=None if all_followings else tagid)
    local_authors = db.get_authors_by_tag(tagid, all_followings)
    merged_map = {int(a.get("mid")): a for a in local_authors if a.get("mid")}
    for it in items:
        m = int(it.get("mid") or 0)
        if m:
            merged_map[m] = it
    merged_list = list(merged_map.values())
    print(f"[*] 成功获取关注博主列表: 共 {len(merged_list)} 位博主 (已同步至 SQLite)", flush=True)
    return {"total": len(merged_list), "items": merged_list}


class LocalSubscribeRequest(BaseModel):
    input_text: str
    tagid: int = -10


@app.post("/api/relation/subscribe_local")
def subscribe_local_up(req: LocalSubscribeRequest):
    """通过 UID 或空间主页链接添加本地免关注订阅博主（无需在 B 站关注即可监控与爬取）."""
    raw = (req.input_text or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="请输入 UP 主 UID 或空间链接")

    mid = 0
    m_url = re.search(r'space\.bilibili\.com/(\d+)', raw)
    if m_url:
        mid = int(m_url.group(1))
    elif raw.isdigit():
        mid = int(raw)
    else:
        m_num = re.search(r'(\d{4,16})', raw)
        if m_num:
            mid = int(m_num.group(1))

    if not mid:
        raise HTTPException(status_code=400, detail="未能识别有效的 UP 主 UID 或空间链接")

    auth = get_current_auth()
    uname = f"UP_{mid}"
    face = ""
    sign = "本地订阅博主"

    try:
        info_res = BiliApi.get_user_info(auth, mid=mid)
        if info_res.get("code") == 0:
            d = info_res.get("data") or {}
            uname = d.get("name") or uname
            face = d.get("face") or ""
            sign = d.get("sign") or sign
        else:
            # 备用：尝试通过投稿列表第 1 页获取作者名
            v_res = BiliApi.get_user_videos(auth, mid=mid, page=1, page_size=5)
            vlist = ((v_res.get("data") or {}).get("list") or {}).get("vlist") or []
            if vlist and vlist[0].get("author"):
                uname = vlist[0].get("author")
    except Exception:
        pass

    up_entry = {"mid": mid, "uname": uname, "face": face, "sign": sign}
    db.save_authors([up_entry], tagid=req.tagid)
    return {
        "success": True,
        "up": up_entry,
        "message": f"已添加本地订阅博主：{uname} (UID: {mid})"
    }


# ---------------------------------------------------------------------- UP 主合集与两级细粒度监听

class ToggleColMonitorRequest(BaseModel):
    mid: int
    collection_type: str  # 'season' or 'series'
    collection_id: int
    is_monitored: bool


class ToggleUpMonitorRequest(BaseModel):
    mid: int
    is_monitored: bool


@app.get("/api/relation/up_collections")
def get_up_collections(mid: int, force_refresh: bool = False):
    """获取指定 UP 主的所有合集 (Season) 与系列 (Series) 列表，附带独立监听状态."""
    if not mid:
        raise HTTPException(status_code=400, detail="Missing mid")

    auth = get_current_auth()
    up_mon = db.get_author_monitor_status(mid)
    cached = db.get_bili_collections(mid)

    if cached and not force_refresh:
        return {
            "success": True,
            "mid": mid,
            "up_monitored": up_mon == 1,
            "collections": cached
        }

    # 请求 B 站官方合集与系列列表接口
    url = f"https://api.bilibili.com/x/polymer/web-space/seasons_series_list?mid={mid}&page_num=1&page_size=30"
    headers = {"Referer": f"https://space.bilibili.com/{mid}/"}
    try:
        from utils.http_util import get_json
        res = get_json(auth, url, headers=headers)
        data = res.get("data") or {}
        items_lists = data.get("items_lists") or {}
        seasons = items_lists.get("seasons_list") or []
        series = items_lists.get("series_list") or []

        parsed_cols = []
        for s in seasons:
            meta = s.get("meta") or {}
            sid = meta.get("season_id")
            if sid:
                parsed_cols.append({
                    "collection_type": "season",
                    "collection_id": sid,
                    "title": meta.get("name") or meta.get("title") or f"合集_{sid}",
                    "cover": meta.get("cover") or "",
                    "total": int(meta.get("total") or len(s.get("archives") or []) or 0),
                })

        for s in series:
            meta = s.get("meta") or {}
            sid = meta.get("series_id")
            if sid:
                parsed_cols.append({
                    "collection_type": "series",
                    "collection_id": sid,
                    "title": meta.get("name") or f"系列_{sid}",
                    "cover": meta.get("cover") or "",
                    "total": int(meta.get("total") or len(s.get("archives") or []) or 0),
                })

        db.save_bili_collections(mid, parsed_cols)
        final_cols = db.get_bili_collections(mid)
        return {
            "success": True,
            "mid": mid,
            "up_monitored": up_mon == 1,
            "collections": final_cols
        }
    except Exception as e:
        print(f"[!] 拉取 UP 主 ({mid}) 合集列表失败: {e}", flush=True)
        return {
            "success": True,
            "mid": mid,
            "up_monitored": up_mon == 1,
            "collections": cached
        }


@app.post("/api/relation/toggle_collection_monitor")
def toggle_col_monitor(req: ToggleColMonitorRequest):
    """切换指定 UP 主特定合集的独立监听状态 (0=不监听, 1=监听)."""
    ok = db.toggle_bili_collection_monitor(req.mid, req.collection_type, req.collection_id, 1 if req.is_monitored else 0)
    return {"success": ok, "mid": req.mid, "collection_id": req.collection_id, "is_monitored": req.is_monitored}


@app.post("/api/relation/toggle_up_monitor")
def toggle_up_monitor(req: ToggleUpMonitorRequest):
    """切换指定 UP 主的全局投稿监听状态 (0=不监听, 1=监听)."""
    ok = db.toggle_author_monitor(req.mid, 1 if req.is_monitored else 0)
    return {"success": ok, "mid": req.mid, "is_monitored": req.is_monitored}


@app.get("/api/sync/status")
def get_sync_status():
    """查询当前增量同步任务实时进度与状态."""
    return dict(_sync_state)


@app.get("/api/autocheck/status")
def get_autocheck_status():
    """获取后台追更巡检状态（上次巡检时间、发现新视频数、未读追更数、是否运行中）."""
    st = dict(_auto_check_state)
    st["unread_count"] = db.count_unread_videos()
    return st


@app.post("/api/autocheck/run_now")
def run_autocheck_now():
    """手动立即触发一次后台追更巡检."""
    return _run_auto_check_once(trigger="manual")


@app.post("/api/videos/mark_read")
def mark_videos_read_api(req: dict):
    """将指定视频或全部未读追更视频标为已读."""
    bvid = req.get("bvid")
    affected = db.mark_videos_read(bvid=bvid if bvid else None)
    return {
        "success": True,
        "affected": affected,
        "unread_count": db.count_unread_videos()
    }


@app.get("/api/videos/index")
def get_videos_index(
    tagid: int = -10,
    all_followings: bool = False,
    force_refresh: bool = False,
    local_only: bool = False,
    deep_scrape: bool = False,
    max_pages: int = 1,
    search: Optional[str] = None,
    up_mid: Optional[int] = None,
    limit: int = 500,
    offset: int = 0
):
    """
    拉取博主的视频列表，支持 SQLite 本地秒级读取、增量智能同步与单博主全量历史抓取.
    - search: 跨库搜索关键词 (标题 / UP名字 / BV号)
    - up_mid: 单博主过滤
    - force_refresh: 是否强制触发网络增量同步
    - deep_scrape: 单博主模式下是否向后翻页抓取历史全量稿件
    - max_pages: 深度抓取最大翻页数（默认 25 页，最多约 750~1000 条）
    """
    # 1. 如果非强制刷新且非深度全量抓取，优先从 SQLite 本地读取
    local_total = db.count_videos(tagid=tagid, all_followings=all_followings, search_kw=search, up_mid=up_mid)
    if not force_refresh and not deep_scrape and (local_total > 0 or local_only):
        local_videos = db.query_videos(
            tagid=tagid,
            all_followings=all_followings,
            search_kw=search,
            up_mid=up_mid,
            limit=limit,
            offset=offset
        )
        print(f"[*] [本地命中] 从 SQLite 加载 {len(local_videos)}/{local_total} 条视频记录 (0ms)", flush=True)
        return {"from_cache": True, "total": local_total, "videos": local_videos, "sync_status": dict(_sync_state)}

    # 2. 如果强制刷新、深度抓取，或者本地该分组/博主暂无数据，则执行网络同步
    auth = get_current_auth()
    if not auth.is_login:
        local_videos = db.query_videos(tagid=tagid, all_followings=all_followings, search_kw=search, up_mid=up_mid, limit=limit, offset=offset)
        return {"from_cache": True, "total": local_total, "videos": local_videos, "sync_status": dict(_sync_state)}

    # 防并发重入：若已有同步任务在运行，直接返回当前本地数据与同步状态，防止耗尽线程池
    if not _sync_lock.acquire(blocking=False):
        local_videos = db.query_videos(tagid=tagid, all_followings=all_followings, search_kw=search, up_mid=up_mid, limit=limit, offset=offset)
        return {
            "from_cache": True,
            "sync_in_progress": True,
            "sync_status": dict(_sync_state),
            "total": local_total,
            "videos": local_videos
        }

    try:
        scope_name = f"UP主({up_mid})" if up_mid else ("全部关注" if all_followings else f"分组(tagid={tagid})")
        _update_sync_state(True, scope=scope_name, current=0, total=1, message=f"正在获取 {scope_name} 列表...")
        print(f"[*] 开始同步视频: [{scope_name}] (deep_scrape={deep_scrape}) ...", flush=True)

        if up_mid:
            local_ups = [u for u in db.get_authors_by_tag(tagid, all_followings=True) if u.get("mid") == up_mid]
            bloggers = local_ups if local_ups else [{"mid": up_mid, "uname": f"UP_{up_mid}"}]
        elif all_followings:
            succ, msg, bloggers = BiliApi.get_all_followings(auth, vmid=auth.mid)
            if not succ:
                bloggers = db.get_authors_by_tag(tagid, all_followings)
            else:
                db.save_authors(bloggers, tagid=None)
                # 合并本地订阅的博主
                b_map = {int(b.get("mid")): b for b in db.get_authors_by_tag(tagid, True) if b.get("mid")}
                for b in bloggers:
                    b_map[int(b.get("mid"))] = b
                bloggers = list(b_map.values())
        else:
            succ, msg, bloggers = BiliApi.get_all_followings_by_tag(auth, tagid=tagid)
            if not succ:
                bloggers = db.get_authors_by_tag(tagid, all_followings)
            else:
                db.save_authors(bloggers, tagid=tagid)
                b_map = {int(b.get("mid")): b for b in db.get_authors_by_tag(tagid, False) if b.get("mid")}
                for b in bloggers:
                    b_map[int(b.get("mid"))] = b
                bloggers = list(b_map.values())

        if not bloggers:
            return {"from_cache": False, "total": 0, "videos": []}

        seen_bvids = set()
        total_bloggers = len(bloggers)
        target_max_pages = max(1, min(50, max_pages if max_pages > 1 else (25 if (up_mid and deep_scrape) else 1)))

        for idx, up in enumerate(bloggers, 1):
            mid = up.get("mid")
            uname = up.get("uname", f"UP_{mid}")
            _update_sync_state(True, scope=scope_name, current=idx, total=total_bloggers, message=f"[{idx}/{total_bloggers}] 正在同步 {uname}")
            latest_known, _ = db.get_author_sync_meta(mid)

            up_new_videos = []
            max_created_this_time = latest_known
            try:
                page = 1
                while page <= target_max_pages:
                    if target_max_pages > 1:
                        _update_sync_state(
                            True,
                            scope=scope_name,
                            current=page,
                            total=target_max_pages,
                            message=f"正在深度抓取 {uname} 第 {page}/{target_max_pages} 页..."
                        )
                    res_json = BiliApi.get_user_videos(auth, mid=mid, page=page, page_size=30)
                    if res_json.get("code") != 0:
                        msg = res_json.get('message', '未知错误')
                        code = res_json.get('code')
                        print(f"    [{idx}/{total_bloggers}] 博主 {uname} 第 {page} 页响应异常 (code={code}): {msg}", flush=True)
                        break

                    data = res_json.get("data") or {}
                    vlist = (data.get("list") or {}).get("vlist") or []
                    total_count = int(((data.get("page") or {}).get("count")) or 0)
                    if not vlist:
                        break

                    stop_incremental = False
                    for item in vlist:
                        bvid = item.get("bvid", "")
                        created = int(item.get("created") or 0)
                        if not bvid or bvid in seen_bvids:
                            continue

                        # ★ 增量截断策略：非深度全量抓取时，遇到已存在的时间戳即停止当前博主后续检索
                        if not deep_scrape and latest_known > 0 and created <= latest_known:
                            stop_incremental = True
                            break

                        seen_bvids.add(bvid)
                        max_created_this_time = max(max_created_this_time, created)
                        up_new_videos.append(_normalize_arc_video(item, mid=mid, default_author=uname))

                    if stop_incremental or len(vlist) < 30 or (total_count > 0 and page * 30 >= total_count):
                        break
                    page += 1

                if up_new_videos:
                    is_read_flag = 0 if (latest_known > 0 and not deep_scrape) else 1
                    db.upsert_videos(up_new_videos, is_read_default=is_read_flag)
                    db.update_author_sync_meta(mid, max_created_this_time)
                    if latest_known > 0 and not deep_scrape and db.get_setting("auto_download_new", "false").lower() == "true":
                        def_v_q = db.get_setting("default_video_quality", "1080P")
                        def_a_q = db.get_setting("default_audio_quality", "30280")
                        download_manager.add_batch_tasks(auth, up_new_videos, quality=def_v_q, audio_only=False, audio_quality=def_a_q)
                    print(f"    [{idx}/{total_bloggers}] 博主 {uname} 同步入库 {len(up_new_videos)} 条稿件", flush=True)
                else:
                    db.update_author_sync_meta(mid, max_created_this_time)
                    print(f"    [{idx}/{total_bloggers}] 博主 {uname} 已是最新 (无新投稿)", flush=True)
            except Exception as e:
                print(f"    [{idx}/{total_bloggers}] 博主 {uname} 网络请求异常: {e}", flush=True)

        # 同步完成后统一从 SQLite 查询
        final_total = db.count_videos(tagid=tagid, all_followings=all_followings, search_kw=search, up_mid=up_mid)
        final_videos = db.query_videos(
            tagid=tagid,
            all_followings=all_followings,
            search_kw=search,
            up_mid=up_mid,
            limit=limit,
            offset=offset
        )
        print(f"[*] 增量同步完成: 本地 SQLite 现有 {final_total} 条视频记录", flush=True)
        return {"from_cache": False, "total": final_total, "videos": final_videos, "sync_status": dict(_sync_state)}
    finally:
        _update_sync_state(False, scope="", current=0, total=0, message="空闲")
        _sync_lock.release()


# ---------------------------------------------------------------------- Favorites (收藏夹)

@app.get("/api/fav/folders")
def get_fav_folders(force_refresh: bool = False):
    """获取当前登录用户的收藏夹列表 (优先读 SQLite，支持增量/强制刷新)."""
    # 1. 尝试从本地 SQLite 读取
    if not force_refresh:
        local_folders = db.get_fav_folders()
        if local_folders:
            return {"from_cache": True, "folders": local_folders}

    # 2. 从 B 站拉取
    auth = get_current_auth()
    if not auth.is_login:
        local_folders = db.get_fav_folders()
        return {"from_cache": True, "folders": local_folders}

    try:
        res = BiliInteractApi.get_fav_folders(auth)
        if res.get("code") == 0:
            folders = (res.get("data") or {}).get("list") or []
            db.save_fav_folders(folders)
            print(f"[*] 成功获取收藏夹列表: 共 {len(folders)} 个收藏夹 (已入库 SQLite)", flush=True)
            return {"from_cache": False, "folders": db.get_fav_folders()}
        else:
            raise HTTPException(status_code=500, detail=f"B站接口错误: {res.get('message')}")
    except Exception as e:
        local_folders = db.get_fav_folders()
        if local_folders:
            return {"from_cache": True, "folders": local_folders}
        raise HTTPException(status_code=500, detail=f"获取收藏夹失败: {str(e)}")


@app.get("/api/fav/videos")
def get_fav_videos(
    media_id: int,
    force_refresh: bool = False,
    local_only: bool = False,
    search: str = "",
    limit: int = 500,
    offset: int = 0
):
    """
    检索指定收藏夹内的视频列表 (本地 SQLite 优先 + 智能增量网络同步).
    - media_id: 收藏夹 ID
    - force_refresh: 是否强制向 B 站拉取增量视频
    - search: 跨库搜索关键词
    """
    # 1. 如果非强制刷新，优先从 SQLite 本地读取
    local_total = db.count_fav_videos(media_id, search_kw=search)
    if not force_refresh and (local_total > 0 or local_only):
        local_videos = db.query_fav_videos(media_id, search_kw=search, limit=limit, offset=offset)
        print(f"[*] [本地命中] 从 SQLite 加载收藏夹({media_id}) {len(local_videos)}/{local_total} 条视频 (0ms)", flush=True)
        return {"from_cache": True, "total": local_total, "videos": local_videos, "sync_status": dict(_sync_state)}

    # 2. 从 B 站进行智能增量抓取
    auth = get_current_auth()
    if not auth.is_login:
        local_videos = db.query_fav_videos(media_id, search_kw=search, limit=limit, offset=offset)
        return {"from_cache": True, "total": local_total, "videos": local_videos, "sync_status": dict(_sync_state)}

    if not _sync_lock.acquire(blocking=False):
        local_videos = db.query_fav_videos(media_id, search_kw=search, limit=limit, offset=offset)
        return {
            "from_cache": True,
            "sync_in_progress": True,
            "sync_status": dict(_sync_state),
            "total": local_total,
            "videos": local_videos
        }

    try:
        scope_name = f"收藏夹({media_id})"
        latest_known_fav, _ = db.get_fav_folder_sync_meta(media_id)
        _update_sync_state(True, scope=scope_name, current=1, total=50, message=f"正在同步 {scope_name} 第 1 页...")
        print(f"[*] 开始增量同步收藏夹({media_id}) (已知最新收藏时间: {latest_known_fav}) ...", flush=True)

        page = 1
        page_size = 20
        max_fav_time_this_sync = latest_known_fav
        fetched_videos = []
        seen_bvids = set()
        stop_incremental = False

        while page <= 50:  # 最多翻 50 页 (1000 个视频)
            try:
                _update_sync_state(True, scope=scope_name, current=page, total=50, message=f"正在同步 {scope_name} 第 {page} 页...")
                res = BiliInteractApi.get_fav_resources(auth, media_id=media_id, page=page, page_size=page_size)
                if res.get("code") != 0:
                    print(f"[!] 收藏夹({media_id}) 第 {page} 页获取异常: {res.get('message')}", flush=True)
                    break

                data = res.get("data") or {}
                medias = data.get("medias") or []
                has_more = data.get("has_more", False)
                if not medias:
                    break

                for m in medias:
                    bvid = m.get("bvid") or m.get("bv_id") or ""
                    if not bvid or bvid in seen_bvids:
                        continue

                    fav_time = m.get("fav_time", 0)
                    # ★ 增量截断策略：一旦遇到已知已入库的时间戳，且数据库已记录过，停止向后翻页
                    if latest_known_fav > 0 and fav_time <= latest_known_fav:
                        stop_incremental = True
                        break

                    seen_bvids.add(bvid)
                    max_fav_time_this_sync = max(max_fav_time_this_sync, fav_time)
                    fetched_videos.append(_normalize_fav_media(m))

                if stop_incremental or not has_more:
                    break

                page += 1
            except Exception as e:
                print(f"[!] 增量同步收藏夹异常: {e}", flush=True)
                break

        if fetched_videos:
            db.save_fav_videos(media_id, fetched_videos)
            db.update_fav_folder_sync_meta(media_id, max_fav_time_this_sync)
            if latest_known_fav > 0 and db.get_setting("auto_download_new", "false").lower() == "true":
                def_v_q = db.get_setting("default_video_quality", "1080P")
                def_a_q = db.get_setting("default_audio_quality", "30280")
                download_manager.add_batch_tasks(auth, fetched_videos, quality=def_v_q, audio_only=False, audio_quality=def_a_q)
            print(f"[*] 收藏夹({media_id}) 增量同步发现 {len(fetched_videos)} 个新增收藏", flush=True)
        else:
            db.update_fav_folder_sync_meta(media_id, max_fav_time_this_sync)
            print(f"[*] 收藏夹({media_id}) 已是最新 (无新视频)", flush=True)

        final_total = db.count_fav_videos(media_id, search_kw=search)
        final_videos = db.query_fav_videos(media_id, search_kw=search, limit=limit, offset=offset)
        return {"from_cache": False, "total": final_total, "videos": final_videos, "sync_status": dict(_sync_state)}
    finally:
        _update_sync_state(False, scope="", current=0, total=0, message="空闲")
        _sync_lock.release()


# ---------------------------------------------------------------------- URL & Collection Parsing

class ParseUrlRequest(BaseModel):
    url: str

class ParseCollectionRequest(BaseModel):
    mid: Optional[int] = None
    series_id: Optional[int] = None
    season_id: Optional[int] = None
    page: int = 1
    page_size: int = 30


@app.post("/api/parse/url")
def parse_url(req: ParseUrlRequest):
    """解析单条 URL、分享文本、BV号、分P及合集元数据."""
    raw_text = req.url.strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="URL 不能为空")

    auth = get_current_auth()

    # 1. 展开 b23.tv 短链接
    short_match = re.search(r'https?://b23\.tv/[a-zA-Z0-9]+', raw_text)
    if short_match:
        try:
            import urllib.request
            req_obj = urllib.request.Request(
                short_match.group(0),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req_obj, timeout=10) as resp:
                raw_text = resp.geturl()
        except Exception:
            pass

    # 2. 提取 BV 号或 av 号
    bv_match = re.search(r'(BV[a-zA-Z0-9]{10})', raw_text, re.IGNORECASE)
    if bv_match:
        bvid = bv_match.group(1)
        if not bvid.startswith("BV"):
            bvid = "BV" + bvid[2:]
    else:
        av_match = re.search(r'av(\d+)', raw_text, re.IGNORECASE)
        if av_match:
            try:
                from utils.bv import enc
                bvid = enc(int(av_match.group(1)))
            except Exception:
                bvid = ""
        else:
            bvid = ""

    if not bvid:
        # 2.5 检查是否为 UP 主合集 (Season) 或系列 (Series) 链接
        # 常见格式：
        # - https://space.bilibili.com/123456/channel/collectiondetail?sid=7890
        # - https://space.bilibili.com/123456/channel/seriesdetail?sid=7890
        # - https://space.bilibili.com/123456/lists/7890?type=season (或 series)
        mid_match = re.search(r'space\.bilibili\.com/(\d+)', raw_text)
        sid_match = (
            re.search(r'[?&](?:sid|season_id|series_id)=(\d+)', raw_text)
            or re.search(r'/lists/(\d+)', raw_text)
        )
        if mid_match and sid_match:
            c_mid = int(mid_match.group(1))
            c_sid = int(sid_match.group(1))
            is_series = ("series" in raw_text.lower()) and ("season" not in raw_text.lower())
            col_req = ParseCollectionRequest(
                mid=c_mid,
                series_id=c_sid if is_series else None,
                season_id=None if is_series else c_sid,
                page=1,
                page_size=100
            )
            col_res = parse_collection(col_req)
            items = col_res.get("items") or []
            if not items and not is_series:
                # 若按 season 未查到，自动回退尝试 series
                col_res = parse_collection(ParseCollectionRequest(mid=c_mid, series_id=c_sid, page=1, page_size=100))
                items = col_res.get("items") or []
            if items:
                first_ep = items[0]
                first_bvid = first_ep.get("bvid", "")
                author_name = first_ep.get("author") or f"UP_{c_mid}"
                return {
                    "success": True,
                    "bvid": first_bvid,
                    "aid": first_ep.get("aid"),
                    "title": f"【合集/系列】共 {len(items)} 部视频 - 首集: {first_ep.get('title', '')}",
                    "author": author_name,
                    "up_mid": c_mid,
                    "face": "",
                    "pic": first_ep.get("pic", ""),
                    "desc": f"从合集/系列链接 (MID={c_mid}, SID={c_sid}) 解析出 {len(items)} 部作品",
                    "pubdate": first_ep.get("pubdate", ""),
                    "duration": first_ep.get("duration", ""),
                    "url": raw_text,
                    "parts": [],
                    "season": {
                        "id": c_sid,
                        "title": f"合集/系列 #{c_sid}",
                        "cover": first_ep.get("pic", ""),
                        "mid": c_mid,
                        "ep_count": len(items),
                        "episodes": items
                    }
                }

        # 2.6 检查是否为外部公开收藏夹链接 (例如 space.bilibili.com/123/favlist?fid=456 或 medialist/detail/ml456)
        fav_match = re.search(r'(?:[?&]fid=|/ml)(\d+)', raw_text)
        if fav_match:
            fid = int(fav_match.group(1))
            fav_res = BiliInteractApi.get_fav_resources(auth, media_id=fid, page=1, page_size=20)
            if fav_res.get("code") == 0:
                f_data = fav_res.get("data") or {}
                f_info = f_data.get("info") or {}
                f_medias = f_data.get("medias") or []
                episodes = [_normalize_fav_media(m) for m in f_medias if (m.get("bvid") or m.get("bv_id"))]
                if episodes:
                    first_ep = episodes[0]
                    folder_title = f_info.get("title") or f"收藏夹 #{fid}"
                    upper_name = (f_info.get("upper") or {}).get("name") or first_ep.get("author") or "收藏夹创建者"
                    return {
                        "success": True,
                        "bvid": first_ep.get("bvid", ""),
                        "aid": first_ep.get("aid"),
                        "title": f"【公开收藏夹】{folder_title} (本页 {len(episodes)} 条)",
                        "author": upper_name,
                        "up_mid": (f_info.get("upper") or {}).get("mid", 0),
                        "face": "",
                        "pic": f_info.get("cover") or first_ep.get("pic", ""),
                        "desc": f"从公开收藏夹 (FID={fid}) 解析出 {len(episodes)} 部视频",
                        "pubdate": first_ep.get("pubdate", ""),
                        "duration": first_ep.get("duration", ""),
                        "url": raw_text,
                        "parts": [],
                        "season": {
                            "id": fid,
                            "title": folder_title,
                            "cover": f_info.get("cover") or first_ep.get("pic", ""),
                            "mid": (f_info.get("upper") or {}).get("mid", 0),
                            "ep_count": len(episodes),
                            "episodes": episodes
                        }
                    }

        raise HTTPException(status_code=400, detail="未能在输入中提取到有效的 B站视频、合集或公开收藏夹链接")

    # 3. 请求 B站 web-interface/view 获取视频详细信息及分P
    try:
        from utils.http_util import get_json
        headers = {"Referer": "https://www.bilibili.com/"}
        view_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        res = get_json(auth, view_url, headers=headers)
        if res.get("code") != 0:
            raise HTTPException(status_code=400, detail=f"B站解析错误: {res.get('message')}")

        data = res.get("data") or {}
        owner = data.get("owner") or {}
        pages = data.get("pages") or []
        ugc_season = data.get("ugc_season") or {}

        parsed_pages = []
        for p in pages:
            dur = p.get("duration", 0)
            parsed_pages.append({
                "cid": p.get("cid"),
                "page": p.get("page"),
                "part": p.get("part", "").strip(),
                "duration": f"{dur // 60}:{dur % 60:02d}" if dur else "",
                "duration_seconds": dur
            })

        duration_sec = data.get("duration", 0)
        length_str = f"{duration_sec // 60}:{duration_sec % 60:02d}" if duration_sec else ""
        pubtime = data.get("pubdate", 0)
        pubdate_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(pubtime)) if pubtime else ""

        season_info = None
        if ugc_season:
            episodes = []
            for sec in ugc_season.get("sections", []):
                for ep in sec.get("episodes", []):
                    arc = ep.get("arc") or {}
                    ep_dur = arc.get("duration", 0)
                    episodes.append({
                        "bvid": ep.get("bvid", ""),
                        "aid": ep.get("aid", 0),
                        "cid": ep.get("cid", 0),
                        "title": ep.get("title", ""),
                        "pic": arc.get("pic", ""),
                        "duration": f"{ep_dur // 60}:{ep_dur % 60:02d}" if ep_dur else "",
                        "pubdate": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(arc.get("pubdate", 0))) if arc.get("pubdate") else ""
                    })

            season_info = {
                "id": ugc_season.get("id"),
                "title": ugc_season.get("title"),
                "cover": ugc_season.get("cover"),
                "mid": ugc_season.get("mid"),
                "ep_count": ugc_season.get("ep_count", len(episodes)),
                "episodes": episodes
            }

        return {
            "success": True,
            "bvid": bvid,
            "aid": data.get("aid"),
            "title": data.get("title", "").strip(),
            "author": owner.get("name", "").strip(),
            "up_mid": owner.get("mid", 0),
            "face": owner.get("face", ""),
            "pic": data.get("pic", ""),
            "desc": data.get("desc", ""),
            "pubdate": pubdate_str,
            "duration": length_str,
            "url": f"https://www.bilibili.com/video/{bvid}",
            "parts": parsed_pages,
            "season": season_info
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析视频异常: {str(e)}")


@app.post("/api/parse/collection")
def parse_collection(req: ParseCollectionRequest):
    """解析 UP 主合集或系列视频列表."""
    auth = get_current_auth()
    try:
        from utils.http_util import get_json
        headers = {"Referer": "https://www.bilibili.com/"}
        items = []

        if req.season_id and req.mid:
            url = f"https://api.bilibili.com/x/polymer/web-space/seasons_archives_list?mid={req.mid}&season_id={req.season_id}&page_num={req.page}&page_size={req.page_size}"
            res = get_json(auth, url, headers=headers)
            if res.get("code") == 0:
                archives = (res.get("data") or {}).get("archives") or []
                for arc in archives:
                    dur = arc.get("duration", 0)
                    items.append({
                        "bvid": arc.get("bvid"),
                        "aid": arc.get("aid"),
                        "title": arc.get("title"),
                        "pic": arc.get("pic"),
                        "pubdate": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(arc.get("pubdate", 0))) if arc.get("pubdate") else "",
                        "duration": f"{dur // 60}:{dur % 60:02d}" if dur else "",
                        "author": (arc.get("author") or {}).get("name", "") if isinstance(arc.get("author"), dict) else ""
                    })
        elif req.series_id and req.mid:
            url = f"https://api.bilibili.com/x/series/archives?mid={req.mid}&series_id={req.series_id}&pn={req.page}&ps={req.page_size}&only_normal=true&sort=desc"
            res = get_json(auth, url, headers=headers)
            if res.get("code") == 0:
                archives = (res.get("data") or {}).get("archives") or []
                for arc in archives:
                    dur = arc.get("duration", 0)
                    items.append({
                        "bvid": arc.get("bvid"),
                        "aid": arc.get("aid"),
                        "title": arc.get("title"),
                        "pic": arc.get("pic"),
                        "pubdate": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(arc.get("pubdate", 0))) if arc.get("pubdate") else "",
                        "duration": f"{dur // 60}:{dur % 60:02d}" if dur else ""
                    })

        return {"success": True, "total": len(items), "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析合集异常: {str(e)}")


# ---------------------------------------------------------------------- Proxy & Open APIs

@app.get("/api/proxy/image")
def proxy_image(url: str):
    """B站图片防盗链代理 (解决部分环境直接请求 403 问题，绕过爬虫令牌桶避免阻塞界面)."""
    if not url:
        raise HTTPException(status_code=400, detail="Missing url")
    if url.startswith("//"):
        url = "https:" + url
    auth = get_current_auth()
    try:
        from utils.http_util import request
        referer = "https://www.zhihu.com/" if ("zhimg.com" in url or "zhihu.com" in url) else "https://www.bilibili.com/"
        resp = request(auth, "get", url, headers={"Referer": referer}, timeout=8, bypass_limiter=True)
        content_type = resp.headers.get("content-type", "image/jpeg")
        return Response(content=resp.content, media_type=content_type, headers={"Cache-Control": "public, max-age=604800"})
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/open/folder")
def open_folder(req: dict):
    """跨平台在文件管理器中打开并定位指定文件或下载目录."""
    import subprocess
    file_path = req.get("file_path", "")
    target_dir = db.get_download_dir() or str(DOWNLOAD_DIR)
    if file_path and os.path.exists(file_path):
        abs_file = os.path.abspath(file_path)
        if os.path.isdir(abs_file):
            if sys.platform == "win32":
                subprocess.Popen(["explorer", abs_file])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", abs_file])
            else:
                subprocess.Popen(["xdg-open", abs_file])
            return {"success": True}
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{abs_file}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", abs_file])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(abs_file)])
        return {"success": True}
    elif os.path.exists(target_dir):
        abs_dir = os.path.abspath(target_dir)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", abs_dir])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", abs_dir])
        else:
            subprocess.Popen(["xdg-open", abs_dir])
        return {"success": True}
    return {"success": False, "message": "目录不存在"}


def _pick_folder_win32(initial_dir: str, title: str = "请选择下载保存根目录（系统将自动按 bilibili / zhihu 平台一级分类）") -> str:
    """调用 Windows 原生 Shell 文件夹选择对话框 (SHBrowseForFolderW)，零外部依赖且支持新建文件夹."""
    import ctypes
    from ctypes import wintypes

    BIF_RETURNONLYFSDIRS = 0x0001
    BIF_EDITBOX = 0x0010
    BIF_NEWDIALOGSTYLE = 0x0040
    BFFM_INITIALIZED = 1
    BFFM_SETSELECTIONW = 0x0400 + 103

    BFFCALLBACK = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.UINT, wintypes.LPARAM, wintypes.LPARAM)

    class BROWSEINFOW(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT),
            ("lpfn", BFFCALLBACK),
            ("lParam", wintypes.LPARAM),
            ("iImage", ctypes.c_int),
        ]

    init_path = os.path.abspath(initial_dir) if initial_dir and os.path.exists(initial_dir) else ""
    init_buf = ctypes.create_unicode_buffer(init_path) if init_path else None

    def _browse_cb(hwnd, uMsg, lParam, lpData):
        if uMsg == BFFM_INITIALIZED and init_buf is not None:
            ctypes.windll.user32.SendMessageW(
                hwnd, BFFM_SETSELECTIONW, 1, ctypes.cast(init_buf, wintypes.LPARAM)
            )
        return 0

    cb_func = BFFCALLBACK(_browse_cb)
    display_buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    bi = BROWSEINFOW()
    bi.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
    bi.pidlRoot = None
    bi.pszDisplayName = ctypes.cast(display_buf, wintypes.LPWSTR)
    bi.lpszTitle = title
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX
    bi.lpfn = cb_func
    bi.lParam = 0
    bi.iImage = 0

    ctypes.windll.ole32.CoInitialize(None)
    try:
        shell32 = ctypes.windll.shell32
        shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return ""
        out_buf = ctypes.create_unicode_buffer(32768)
        ok = shell32.SHGetPathFromIDListW(ctypes.c_void_p(pidl), out_buf)
        ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
        return out_buf.value if ok else ""
    finally:
        ctypes.windll.ole32.CoUninitialize()


@app.post("/api/system/select_folder")
def select_folder(req: Optional[dict] = None):
    """唤起系统原生文件夹选择对话框，供用户直观选择下载保存目录."""
    import subprocess
    payload = req or {}
    initial_dir = str(payload.get("initial_dir") or db.get_download_dir() or DOWNLOAD_DIR)
    selected = ""
    try:
        if sys.platform == "win32":
            selected = _pick_folder_win32(initial_dir)
        elif sys.platform == "darwin":
            proc = subprocess.run(
                ["osascript", "-e", "POSIX path of (choose folder)"],
                capture_output=True, text=True, timeout=120
            )
            if proc.returncode == 0:
                selected = proc.stdout.strip()
        else:
            proc = subprocess.run(
                ["zenity", "--file-selection", "--directory"],
                capture_output=True, text=True, timeout=120
            )
            if proc.returncode == 0:
                selected = proc.stdout.strip()
    except Exception as e:
        return {"success": False, "path": "", "cancelled": False, "message": str(e)}

    if not selected:
        return {"success": True, "path": "", "cancelled": True}

    clean_selected = os.path.abspath(selected.strip())
    return {"success": True, "path": clean_selected, "cancelled": False}


@app.post("/api/open/file")
def open_file(req: dict):
    """跨平台直接使用系统默认关联程序打开本地音频或视频文件."""
    import subprocess
    file_path = req.get("file_path", "")
    if file_path and os.path.exists(file_path):
        abs_file = os.path.abspath(file_path)
        if sys.platform == "win32":
            os.startfile(abs_file)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", abs_file])
        else:
            subprocess.Popen(["xdg-open", abs_file])
        return {"success": True}
    return {"success": False, "message": "文件不存在"}


# ---------------------------------------------------------------------- Settings APIs

class SettingsUpdateRequest(BaseModel):
    download_dir: Optional[str] = None
    download_max_workers: Optional[int] = None
    refresh_on_select: Optional[bool] = None
    auto_check_enabled: Optional[bool] = None
    auto_check_interval: Optional[int] = None
    auto_check_scope: Optional[str] = None
    auto_download_new: Optional[bool] = None
    default_video_quality: Optional[str] = None
    default_audio_quality: Optional[str] = None
    block_paid_videos: Optional[bool] = None
    theme: Optional[str] = None
    disclaimer_accepted: Optional[bool] = None


@app.get("/api/settings")
def get_settings():
    """获取系统所有持久化配置项."""
    cfg = db.get_all_settings()
    return {
        "download_dir": cfg.get("download_dir", db.get_download_dir()),
        "download_max_workers": int(cfg.get("download_max_workers", download_manager.max_workers)),
        "refresh_on_select": cfg.get("refresh_on_select", "false").lower() == "true",
        "auto_check_enabled": cfg.get("auto_check_enabled", "false").lower() == "true",
        "auto_check_interval": int(cfg.get("auto_check_interval", 30)),
        "auto_check_scope": cfg.get("auto_check_scope", "special"),
        "auto_download_new": cfg.get("auto_download_new", "false").lower() == "true",
        "default_video_quality": cfg.get("default_video_quality", "1080P"),
        "default_audio_quality": cfg.get("default_audio_quality", "30280"),
        "block_paid_videos": cfg.get("block_paid_videos", "true").lower() == "true",
        "theme": cfg.get("theme", "light"),
        "disclaimer_accepted": cfg.get("disclaimer_accepted", "false").lower() == "true",
        "database_file": str(db.DB_FILE),
        "zhihu_database_file": str(db._get_zhihu_auth_db_path())
    }


@app.post("/api/settings")
def update_settings(req: SettingsUpdateRequest):
    """更新系统配置项并即时应用."""
    to_update = {}
    if req.download_dir:
        clean_dir = os.path.abspath(req.download_dir.strip())
        os.makedirs(clean_dir, exist_ok=True)
        os.makedirs(os.path.join(clean_dir, "bilibili"), exist_ok=True)
        os.makedirs(os.path.join(clean_dir, "zhihu"), exist_ok=True)
        to_update["download_dir"] = clean_dir
        print(f"[*] 自定义下载保存目录已更新: {clean_dir}", flush=True)

    if req.download_max_workers is not None:
        workers = max(1, min(16, req.download_max_workers))
        to_update["download_max_workers"] = str(workers)
        download_manager.set_max_workers(workers)

    if req.refresh_on_select is not None:
        to_update["refresh_on_select"] = "true" if req.refresh_on_select else "false"

    if req.auto_check_enabled is not None:
        to_update["auto_check_enabled"] = "true" if req.auto_check_enabled else "false"

    if req.auto_check_interval is not None:
        to_update["auto_check_interval"] = str(max(5, req.auto_check_interval))

    if req.auto_check_scope is not None:
        to_update["auto_check_scope"] = req.auto_check_scope

    if req.auto_download_new is not None:
        to_update["auto_download_new"] = "true" if req.auto_download_new else "false"

    if req.default_video_quality is not None:
        to_update["default_video_quality"] = req.default_video_quality.strip()

    if req.default_audio_quality is not None:
        to_update["default_audio_quality"] = req.default_audio_quality.strip()

    if req.block_paid_videos is not None:
        to_update["block_paid_videos"] = "true" if req.block_paid_videos else "false"

    if req.theme is not None:
        to_update["theme"] = req.theme

    if req.disclaimer_accepted is not None:
        to_update["disclaimer_accepted"] = "true" if req.disclaimer_accepted else "false"

    db.update_settings(to_update)
    return {"success": True, "settings": get_settings()}


# ---------------------------------------------------------------------- Rate Limiter & Token Bucket APIs

class LimiterConfigRequest(BaseModel):
    platform: Optional[str] = "bili"
    rate: Optional[float] = None
    capacity: Optional[float] = None
    min_interval: Optional[float] = None
    jitter: Optional[float] = None
    ban_cooldown: Optional[float] = None
    max_retries: Optional[int] = None
    is_paused: Optional[bool] = None


class LimiterPauseRequest(BaseModel):
    platform: Optional[str] = "bili"
    paused: Optional[bool] = None


def _get_target_limiter(platform: str = "bili"):
    return zhihu_limiter if (platform or "").lower() == "zhihu" else bili_limiter


@app.get("/api/limiter/status")
def get_limiter_status(platform: str = "bili"):
    """获取令牌桶与请求队列实时监控数据与指标."""
    target = _get_target_limiter(platform)
    return target.get_status()


@app.get("/api/limiter/config")
def get_limiter_config(platform: str = "bili"):
    """获取令牌桶与风控当前配置."""
    target = _get_target_limiter(platform)
    return target.get_config()


@app.post("/api/limiter/config")
def update_limiter_config(req: LimiterConfigRequest):
    """动态更新限流器配置并保存至 SQLite."""
    target = _get_target_limiter(req.platform or "bili")
    updated = target.update_config(
        rate=req.rate,
        capacity=req.capacity,
        min_interval=req.min_interval,
        jitter=req.jitter,
        ban_cooldown=req.ban_cooldown,
        max_retries=req.max_retries,
        is_paused=req.is_paused
    )
    setting_key = "zhihu_rate_limit_config" if (req.platform or "").lower() == "zhihu" else "rate_limit_config"
    db.set_setting(setting_key, json.dumps(updated))
    print(f"[*] [{target.platform_name}] 令牌桶限流配置已更新: {updated}", flush=True)
    return {"success": True, "platform": target.platform_name, "config": updated}


@app.post("/api/limiter/pause")
def toggle_limiter_pause(req: LimiterPauseRequest):
    """一键暂停/恢复请求队列."""
    target = _get_target_limiter(req.platform or "bili")
    target_state = req.paused if req.paused is not None else not target.is_paused
    target.update_config(is_paused=target_state)
    return {"success": True, "platform": target.platform_name, "is_paused": target.is_paused}


@app.post("/api/limiter/reset_stats")
def reset_limiter_stats(req: Optional[LimiterPauseRequest] = None):
    """重置限流器统计数据与日志."""
    platform = (req.platform if req else "bili") or "bili"
    target = _get_target_limiter(platform)
    target.reset_stats()
    return {"success": True, "platform": target.platform_name}


# ---------------------------------------------------------------------- Download Queue & Manager APIs

@app.post("/api/download/single")
def download_single(req: DownloadSingleRequest):
    """将单个视频或音频任务提交到多线程下载队列."""
    auth = get_current_auth()
    bvid = req.video.get("bvid")
    if not bvid:
        raise HTTPException(status_code=400, detail="缺少 bvid")

    task = download_manager.add_task(
        auth,
        req.video,
        quality=req.quality,
        audio_only=req.audio_only,
        audio_quality=req.audio_quality,
        block_paid=req.block_paid,
    )
    return {"success": True, "bvid": bvid, "task": task.to_dict()}


@app.post("/api/download/batch")
def download_batch(req: DownloadBatchRequest):
    """批量提交下载任务到多线程队列 (支持并发+失败重试)."""
    auth = get_current_auth()
    videos = req.videos
    if not videos:
        raise HTTPException(status_code=400, detail="没有视频任务")

    tasks = download_manager.add_batch_tasks(
        auth,
        videos,
        quality=req.quality,
        audio_only=req.audio_only,
        audio_quality=req.audio_quality,
        block_paid=req.block_paid,
    )
    return {"success": True, "count": len(tasks), "message": f"已将 {len(tasks)} 个任务提交至多线程队列"}


@app.get("/api/download/tasks")
def get_download_tasks():
    """获取所有下载任务的实时进度与历史持久化记录."""
    cur_bili = db.get_current_account()
    bili_acc_name = (cur_bili or {}).get("uname") or "B站账号"
    bili_tasks = download_manager.get_all_tasks()
    for bt in bili_tasks:
        if not bt.get("platform"):
            bt["platform"] = "bili"
        if not bt.get("account_name"):
            bt["account_name"] = bili_acc_name
    return {
        "tasks": get_zhihu_archive_tasks() + bili_tasks,
        "active_count": download_manager.get_active_count() + sum(1 for t in get_zhihu_archive_tasks() if t.get("status") in ("pending", "downloading"))
    }


@app.post("/api/download/retry")
def retry_download(req: dict):
    """重试特定失败任务或当前筛选范围内的失败/已取消任务."""
    auth = get_current_auth()
    bvid = req.get("bvid")
    bvids = req.get("bvids")
    all_failed = bool(req.get("all_failed", False))
    include_cancelled = bool(req.get("include_cancelled", False))
    only_cancelled = bool(req.get("only_cancelled", False))

    if isinstance(bvids, list):
        target_ids = [str(x) for x in bvids if x]
        if not target_ids:
            return {"success": True, "count": 0, "message": "当前筛选范围内没有可操作的任务"}
        zhihu_ids = [x for x in target_ids if x.startswith("zhihu_")]
        bili_ids = [x for x in target_ids if not x.startswith("zhihu_")]
        count = 0
        if bili_ids:
            count += download_manager.retry_all_failed(
                auth,
                bvids=bili_ids,
                include_cancelled=include_cancelled,
                only_cancelled=only_cancelled,
            )
        if zhihu_ids:
            count += retry_zhihu_archive_tasks(
                bvids=zhihu_ids,
                include_cancelled=include_cancelled,
                only_cancelled=only_cancelled,
            )
        action_label = "恢复已取消" if only_cancelled else "重新提交失败"
        return {"success": True, "count": count, "message": f"已{action_label} {count} 个任务"}
    elif all_failed or only_cancelled:
        count = download_manager.retry_all_failed(
            auth,
            include_cancelled=include_cancelled,
            only_cancelled=only_cancelled,
        )
        count += retry_zhihu_archive_tasks(
            include_cancelled=include_cancelled,
            only_cancelled=only_cancelled,
        )
        action_label = "恢复已取消" if only_cancelled else "重新提交失败"
        return {"success": True, "count": count, "message": f"已{action_label} {count} 个任务"}
    elif bvid:
        bvid_str = str(bvid)
        if bvid_str.startswith("zhihu_"):
            c = retry_zhihu_archive_tasks(bvids=[bvid_str], include_cancelled=True)
            if c == 0:
                raise HTTPException(status_code=400, detail="重试失败：原任务所属知乎账号可能已退出或禁用，请重新选择账号")
            return {"success": True, "bvid": bvid_str}
        succ = download_manager.retry_task(auth, bvid_str)
        if not succ and download_manager.last_retry_error:
            raise HTTPException(status_code=400, detail=download_manager.last_retry_error)
        return {"success": succ, "bvid": bvid_str, "message": download_manager.last_retry_error or ""}
    return {"success": False, "message": "缺少参数"}


@app.post("/api/download/cancel")
def cancel_download(req: dict):
    """取消特定下载任务并中断底层连接."""
    bvid = req.get("bvid")
    if not bvid:
        raise HTTPException(status_code=400, detail="缺少 bvid")
    succ = download_manager.cancel_task(bvid)
    return {"success": succ, "bvid": bvid, "message": "已取消下载任务" if succ else "未找到可取消的活动任务"}


@app.post("/api/download/clear_completed")
def clear_completed_tasks(req: Optional[dict] = None):
    """从任务列表中清空已完成（或指定状态/范围）的项."""
    payload = req or {}
    bvids = payload.get("bvids")
    statuses = payload.get("statuses")
    if not isinstance(statuses, list) or not statuses:
        statuses = ["success"]
    if isinstance(bvids, list):
        target_ids = [str(x) for x in bvids if x]
        if not target_ids:
            return {"success": True, "count": 0}
        zhihu_ids = [x for x in target_ids if x.startswith("zhihu_")]
        bili_ids = [x for x in target_ids if not x.startswith("zhihu_")]
        count = 0
        if bili_ids:
            count += download_manager.clear_completed(bvids=bili_ids, statuses=statuses)
        if zhihu_ids:
            count += clear_zhihu_archive_tasks(bvids=zhihu_ids, statuses=statuses)
        return {"success": True, "count": count}
    count = download_manager.clear_completed(statuses=statuses)
    count += clear_zhihu_archive_tasks(statuses=statuses)
    return {"success": True, "count": count}


@app.post("/api/download/reconcile")
def manual_reconcile():
    """手动扫描对账磁盘上的文件与数据库记录 (检测用户手动删除或移动的文件)."""
    reset_cnt, restore_cnt = db.reconcile_downloaded_files()
    return {
        "success": True,
        "reset_count": reset_cnt,
        "restored_count": restore_cnt,
        "message": f"磁盘对账自愈完成: 已清理失效记录 {reset_cnt} 条，自动认领现有文件 {restore_cnt} 条"
    }


def start_parent_watchdog(parent_pid: int):
    """守护线程：当主进程退出或销毁后，本后端进程自动随之销毁退出."""
    def _watch():
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        while True:
            time.sleep(1)
            try:
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, parent_pid)
                if not handle:
                    os._exit(0)
                exit_code = ctypes.c_ulong()
                succ = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(handle)
                if not succ or exit_code.value != STILL_ACTIVE:
                    os._exit(0)
            except Exception:
                os._exit(0)

    t = threading.Thread(target=_watch, daemon=True)
    t.start()


# 托管编译后的前端静态资源 (dev/bili-app/dist)
_frontend_dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bili-app", "dist"))
if os.path.exists(_frontend_dist):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend_dist")


if __name__ == "__main__":
    import argparse
    import socket

    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-pid", type=int, default=None, help="Main process PID to watch")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "18888")), help="Backend port (0 for random)")
    parser.add_argument("--token", type=str, default=os.getenv("BILI_AUTH_TOKEN", ""), help="Auth token for HTTP API")
    parser.add_argument("--data-dir", type=str, default=os.getenv("BILI_DATA_DIR", ""), help="Data and download directory")
    cli_args, _ = parser.parse_known_args()

    if cli_args.token:
        AUTH_TOKEN = cli_args.token

    if cli_args.data_dir:
        clean_dir = os.path.abspath(cli_args.data_dir)
        os.environ["BILI_DATA_DIR"] = clean_dir
        DOWNLOAD_DIR = clean_dir
        db.set_data_dir(clean_dir)
        set_storage_dir(clean_dir)
        zhihu_auth.set_storage_dir(clean_dir)

    parent_pid = cli_args.parent_pid or os.getenv("BILI_PARENT_PID")
    if parent_pid:
        try:
            start_parent_watchdog(int(parent_pid))
            print(f"[*] Parent process watchdog attached to PID {parent_pid}", flush=True)
        except Exception as e:
            print(f"[!] Failed to attach parent watchdog: {e}", flush=True)

    port = cli_args.port
    if port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
    else:
        # 仅检测指定端口是否已有服务
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(('127.0.0.1', port)) == 0:
                print(f"[!] 警告: 端口 {port} 已被占用，尝试使用动态端口", flush=True)
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
                    s2.bind(('127.0.0.1', 0))
                    port = s2.getsockname()[1]

    # 输出规范化的 JSON Ready 事件供 Electron / 进程主管消费
    ready_msg = {
        "type": "ready",
        "version": "1.0.0",
        "coreInstanceId": CORE_INSTANCE_ID,
        "port": port,
        "pid": os.getpid()
    }
    print(json.dumps(ready_msg), flush=True)

    try:
        print(f"[*] Starting Bilibili Desktop Backend on http://127.0.0.1:{port}", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    except Exception as e:
        print(f"\n[x] 服务启动异常: {e}", flush=True)
        sys.exit(1)
