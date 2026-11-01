# -*- coding: utf-8 -*-
"""
知乎多平台监听与归档 FastAPI 路由模块 (dev/BilibiliApis-master/zhihu_router.py)
提供：
1. 知乎扫码登录与 Cookie 导入管理 (/api/zhihu/auth/*)
2. 博主（答主）监听管理与增量/深度抓取 (/api/zhihu/creators/*)
3. 收藏夹（我的/关注的/自定义链接）监听与抓取 (/api/zhihu/collections/*)
4. 自己点赞（赞同）的帖子监听与历史回溯 (/api/zhihu/upvotes/*)
5. 单帖/问题链接解析、右侧沉浸阅读详情、批量 Markdown+配图离线归档队列 (/api/zhihu/contents/*, /api/zhihu/archive/*)
"""

import os
import hashlib
import threading
import time
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from utils import db
from utils.logger import app_logger
from zhihu_core import ZhihuClient, ZhihuArchiver, zhihu_auth, zhihu_client

zhihu_router = APIRouter(prefix="/api/zhihu", tags=["zhihu"])

def account_operation(fn):
    """Serialize active-account changes with synchronous requests and bind data ownership."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with zhihu_auth._lock:
            with db.zhihu_workspace(db.zhihu_workspace_token()):
                return fn(*args, **kwargs)
    return wrapped


# 知乎离线归档后台线程池与任务表
_archive_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="zhihu_archiver")
_archive_lock = threading.Lock()
_archive_tasks: Dict[str, Dict[str, Any]] = {}


def _refresh_content_access(item, client: Optional[ZhihuClient] = None):
    if item.get("paid_check_version", 0) >= 2:
        return item
    target_client = client or zhihu_client
    cid = str(item["content_id"])
    if item.get("content_type") == "article":
        fresh = target_client.get_full_article(cid)
    elif item.get("content_type", "answer") == "answer":
        fresh = target_client.get_full_answer(str(item.get("question_id") or ""), cid)
    else:
        fresh = None
    if not fresh or str(fresh.get("content_id")) != cid or fresh.get("paid_check_version", 0) < 2:
        raise HTTPException(status_code=409, detail="暂时无法确认内容的付费状态，请稍后重试或打开原帖查看")
    db.update_zhihu_access_info(cid, fresh)
    return {**item, "is_paid": fresh["is_paid"], "paid_reason": fresh.get("paid_reason", ""),
            "is_restricted": int(bool(fresh["is_paid"])), "restrict_reason": fresh.get("paid_reason", ""),
            "paid_check_version": fresh["paid_check_version"],
            "content_html": fresh.get("content_html") or item.get("content_html", "")}


def _present_content(item):
    if item.get("paid_check_version", 0) < 2:
        return {**item, "is_paid": 0, "is_restricted": 0, "paid_reason": "", "access_pending": True}
    return item


def _submit_archive_task(item: Dict[str, Any], category_folder: Optional[str] = None) -> Dict[str, Any]:
    cid = str(item.get("content_id") or "")
    if not cid:
        raise ValueError("缺少 content_id")

    account_token = db.zhihu_workspace_token()
    credentials = zhihu_auth.snapshot(account_token)
    task_client = ZhihuClient(credentials)
    item = _refresh_content_access(item, client=task_client)

    # 严禁归档知乎盐选与付费受限内容
    if item.get("is_paid") or item.get("is_restricted"):
        reason = item.get("paid_reason") or item.get("restrict_reason") or "知乎盐选/付费受限内容"
        raise ValueError(f"该条目属于{reason}，系统已严格合规拦截，禁止归档。")

    task_key = f"{account_token}:{cid}"
    archiver = ZhihuArchiver(task_client)
    now = int(time.time())
    with _archive_lock:
        existing = _archive_tasks.get(task_key)
        if existing and existing.get("status") in ("pending", "downloading"):
            return existing

        acc_rec = db.get_zhihu_account_by_token(account_token) if account_token else None
        acc_name = (
            (acc_rec or {}).get("uname")
            or (credentials.user_profile or {}).get("name")
            or (account_token if account_token else "知乎账号")
        )
        task_info = {
            "content_id": cid,
            "bvid": f"zhihu_{account_token}_{cid}",  # 兼容统一任务队列展示键
            "platform": "zhihu",
            "account_name": acc_name,
            "account_id": account_token,
            "content_type": item.get("content_type", "answer"),
            "title": item.get("title", f"知乎内容 #{cid}"),
            "author": item.get("author_name", "知乎用户"),
            "group_type": item.get("source_type", "creator"),
            "group_name": item.get("source_title") or item.get("author_name") or "知乎归档",
            "status": "pending",
            "progress": 0,
            "speed": "准备归档...",
            "file_path": item.get("local_file", ""),
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        _archive_tasks[task_key] = task_info

    db.update_zhihu_archive_status(cid, "downloading")

    def _archive_worker():
        def _on_progress(msg: str, pct: int):
            if credentials._revoked.is_set():
                raise RuntimeError("账号已退出、禁用或移除，归档已停止")
            with _archive_lock:
                if task_key in _archive_tasks:
                    _archive_tasks[task_key]["status"] = "downloading"
                    _archive_tasks[task_key]["progress"] = pct
                    _archive_tasks[task_key]["speed"] = msg
                    _archive_tasks[task_key]["updated_at"] = int(time.time())

        try:
            _on_progress("正在准备正文与图片...", 5)
            dl_dir = db.get_download_dir()
            md_path = archiver.archive_item(
                item=dict(item),
                base_download_dir=dl_dir,
                category_folder=category_folder,
                progress_cb=_on_progress,
            )
            db.update_zhihu_archive_status(
                cid,
                "success",
                local_file=md_path,
                content_html=item.get("content_html"),
            )
            with _archive_lock:
                if task_key in _archive_tasks:
                    _archive_tasks[task_key]["status"] = "success"
                    _archive_tasks[task_key]["progress"] = 100
                    _archive_tasks[task_key]["speed"] = "已归档 MD+配图"
                    _archive_tasks[task_key]["file_path"] = md_path
                    _archive_tasks[task_key]["updated_at"] = int(time.time())
        except Exception as e:
            err_str = str(e)
            db.update_zhihu_archive_status(cid, "failed")
            with _archive_lock:
                if task_key in _archive_tasks:
                    _archive_tasks[task_key]["status"] = "failed"
                    _archive_tasks[task_key]["speed"] = "归档失败"
                    _archive_tasks[task_key]["error"] = err_str
                    _archive_tasks[task_key]["updated_at"] = int(time.time())

    def _worker():
        with db.zhihu_workspace(account_token):
            _archive_worker()

    _archive_executor.submit(_worker)
    return task_info


def get_zhihu_archive_tasks() -> List[Dict[str, Any]]:
    with _archive_lock:
        return sorted(_archive_tasks.values(), key=lambda x: x.get("updated_at", 0), reverse=True)


def retry_zhihu_archive_tasks(
    bvids: Optional[List[str]] = None,
    include_cancelled: bool = False,
    only_cancelled: bool = False,
) -> int:
    if only_cancelled:
        allowed_statuses = {"cancelled"}
    elif include_cancelled:
        allowed_statuses = {"failed", "cancelled"}
    else:
        allowed_statuses = {"failed"}
    bvid_filter = set(bvids) if bvids is not None else None

    with _archive_lock:
        candidates = [
            dict(t)
            for t in _archive_tasks.values()
            if t.get("status") in allowed_statuses and (bvid_filter is None or t.get("bvid") in bvid_filter)
        ]

    retried = 0
    with zhihu_auth._lock:
        for t in candidates:
            cid = str(t.get("content_id") or "")
            acc_token = str(t.get("account_id") or "")
            if not cid:
                continue
            try:
                with db.zhihu_workspace(acc_token):
                    item = db.get_zhihu_content_detail(cid)
                    if item:
                        _submit_archive_task(item)
                        retried += 1
            except Exception as e:
                app_logger.warning("[ZhihuRouter] 重试归档任务 %s 失败: %s", cid, e)
    return retried


def clear_zhihu_archive_tasks(
    bvids: Optional[List[str]] = None,
    statuses: Optional[List[str]] = None,
) -> int:
    allowed_statuses = set(statuses or ["success"])
    bvid_filter = set(bvids) if bvids is not None else None
    with _archive_lock:
        to_remove = [
            key
            for key, t in _archive_tasks.items()
            if t.get("status") in allowed_statuses and (bvid_filter is None or t.get("bvid") in bvid_filter)
        ]
        for key in to_remove:
            _archive_tasks.pop(key, None)
        return len(to_remove)


# ==================== 1. 知乎鉴权与扫码登录接口 ====================

def _sync_current_zhihu_to_db(profile: Optional[Dict[str, Any]] = None) -> None:
    """将当前激活的知乎会话同步持久化到 zhihu_accounts 多账号表"""
    prof = profile or zhihu_auth.user_profile or {}
    url_token = str(prof.get("url_token") or "").strip()
    uname = str(prof.get("name") or "").strip()
    if not url_token:
        return
    cookie_str = zhihu_auth.get_cookie_header()
    if cookie_str:
        db.save_zhihu_account(
            url_token=url_token,
            uname=uname or "知乎用户",
            face=str(prof.get("avatar_url") or ""),
            cookie=cookie_str,
            headline=str(prof.get("headline") or ""),
            is_current=1,
        )


def _verify_saved_session():
    # Run off the UI path. Each attempt uses a snapshot and cannot invalidate a
    # newer login that arrived while the network request was outstanding.
    with zhihu_auth._lock:
        if zhihu_auth._verification_running or time.monotonic() < zhihu_auth._verification_next:
            return
        candidate = zhihu_auth.snapshot()
        original_cookie = zhihu_auth.get_cookie_header()
        original_epoch = zhihu_auth._login_epoch
        zhihu_auth._verification_running = True

    def worker():
        try:
            ok, profile = candidate.verify_login()
            with zhihu_auth._lock:
                if original_epoch != zhihu_auth._login_epoch or original_cookie != zhihu_auth.get_cookie_header():
                    return
                if ok and profile and profile.get("url_token") == (zhihu_auth.user_profile or {}).get("url_token"):
                    zhihu_auth.user_profile = profile
                    zhihu_auth.verification_state = "verified"
                    zhihu_auth._save_cookies()
                elif candidate.verification_state == "expired":
                    zhihu_auth.invalidate()
                    zhihu_auth.verification_state = "expired"
                else:
                    zhihu_auth.verification_state = "unavailable"
        finally:
            with zhihu_auth._lock:
                zhihu_auth._verification_running = False
                zhihu_auth._verification_next = time.monotonic() + 30

    threading.Thread(target=worker, daemon=True).start()


@zhihu_router.get("/auth/status")
@account_operation
def zhihu_auth_status():
    logged_out = db.get_setting("zhihu_logged_out", "false") == "true"
    if not logged_out and (not zhihu_auth.is_login or not zhihu_auth.user_profile):
        current = db.get_current_zhihu_account()
        if current and current.get("cookie"):
            cookies = dict(part.strip().split("=", 1) for part in current["cookie"].split(";") if "=" in part)
            if cookies.get("z_c0"):
                zhihu_auth.cookies_dict = cookies
                zhihu_auth.user_profile = {"url_token": current["url_token"], "name": current["uname"],
                                           "avatar_url": current.get("face", ""), "headline": current.get("headline", "")}
                zhihu_auth.verification_state = "pending"
    if not logged_out and zhihu_auth.is_login and zhihu_auth.user_profile:
        _verify_saved_session()
        profile = zhihu_auth.user_profile or {}
        if zhihu_auth.is_login:
            return {"is_login": True, "url_token": profile.get("url_token", ""),
                    "uname": profile.get("name", "知乎用户"), "face": profile.get("avatar_url", ""),
                    "headline": profile.get("headline", ""), "d_c0_ready": bool(zhihu_auth.cookies_dict.get("d_c0")),
                    "verification_state": zhihu_auth.verification_state}
    return {"is_login": False, "url_token": "", "uname": "未登录知乎", "face": "", "d_c0_ready": False,
            "verification_state": "logged_out" if logged_out else zhihu_auth.verification_state}


@zhihu_router.get("/auth/accounts")
@account_operation
def zhihu_list_accounts():
    """获取所有已保存的知乎多账号列表"""
    accounts = db.get_zhihu_accounts()
    if not accounts and zhihu_auth.is_login and zhihu_auth.user_profile:
        _sync_current_zhihu_to_db(zhihu_auth.user_profile)
        accounts = db.get_zhihu_accounts()
    return {"accounts": accounts}


@zhihu_router.post("/auth/switch_account")
@account_operation
def zhihu_switch_account(req: Dict[str, Any]):
    """切换知乎当前激活账号"""
    url_token = str(req.get("url_token") or "").strip()
    acc = db.get_zhihu_account_by_token(url_token)
    if not acc:
        raise HTTPException(status_code=404, detail="知乎账号未找到")
    cookie_str = acc.get("cookie") or ""
    if not cookie_str:
        raise HTTPException(status_code=400, detail="该账号缺少有效 Cookie")
    ok, msg, profile = zhihu_auth.import_cookie_string(cookie_str, url_token)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    zhihu_auth.cancel_login()
    db.set_current_zhihu_account(url_token)
    return {
        "success": True,
        "url_token": (profile or {}).get("url_token") or url_token,
        "uname": (profile or {}).get("name") or acc.get("uname", "知乎用户"),
        "face": (profile or {}).get("avatar_url") or acc.get("face", ""),
        "message": f"已切换至知乎账号: {acc.get('uname')}",
    }


@zhihu_router.post("/auth/remove_account")
@account_operation
def zhihu_remove_account(req: Dict[str, Any]):
    """移除指定知乎账号"""
    url_token = str(req.get("url_token") or "").strip()
    if not db.get_zhihu_account_by_token(url_token):
        raise HTTPException(status_code=404, detail="知乎账号未找到")
    current = db.get_current_zhihu_account()
    active_token = (zhihu_auth.user_profile or {}).get("url_token")
    if active_token == url_token or (current and current["url_token"] == url_token):
        zhihu_auth.logout()
    else:
        zhihu_auth.cancel_login()
    zhihu_auth.revoke_account_tasks(url_token)
    db.remove_zhihu_account(url_token)
    return {"success": True, "message": "知乎账号已移除"}


@zhihu_router.post("/auth/update_account_flags")
@account_operation
def zhihu_update_account_flags(req: Dict[str, Any]):
    """更新知乎账号启用状态与自动刷新开关"""
    url_token = str(req.get("url_token") or "").strip()
    if not url_token:
        raise HTTPException(status_code=400, detail="缺少 url_token")
    if not db.get_zhihu_account_by_token(url_token):
        raise HTTPException(status_code=404, detail="知乎账号未找到")
    is_enabled = req.get("is_enabled")
    auto_refresh = req.get("auto_refresh")
    current = db.get_current_zhihu_account()
    active_token = (zhihu_auth.user_profile or {}).get("url_token")
    if is_enabled is not None and not bool(is_enabled):
        zhihu_auth.revoke_account_tasks(url_token)
        if active_token == url_token or (current and current["url_token"] == url_token):
            zhihu_auth.logout()
        else:
            zhihu_auth.cancel_login()
    db.update_zhihu_account_flags(
        url_token,
        is_enabled=int(bool(is_enabled)) if is_enabled is not None else None,
        auto_refresh=int(bool(auto_refresh)) if auto_refresh is not None else None,
    )
    return {"success": True, "accounts": db.get_zhihu_accounts()}


@zhihu_router.post("/auth/qrcode/generate")
@account_operation
def zhihu_qrcode_generate():
    """生成知乎扫码登录二维码"""
    res = zhihu_auth.generate_qrcode()
    app_logger.info(
        "[ZhihuRouter] /auth/qrcode/generate -> success=%s, key=%s, url=%s",
        res.get("success"),
        res.get("qrcode_key"),
        res.get("qrcode_url"),
    )
    return res


@zhihu_router.post("/auth/qrcode/poll")
@account_operation
def zhihu_qrcode_poll(req: Dict[str, Any]):
    """轮询知乎扫码状态"""
    qrcode_key = req.get("qrcode_key") or ""
    res = zhihu_auth.poll_qrcode(qrcode_key)
    if res.get("status") == "success":
        _sync_current_zhihu_to_db(zhihu_auth.user_profile)
    app_logger.info(
        "[ZhihuRouter] /auth/qrcode/poll(key=%s) -> status=%s, msg=%s",
        qrcode_key,
        res.get("status"),
        res.get("message"),
    )
    return res


@zhihu_router.post("/auth/browser_login")
@account_operation
def zhihu_browser_login_start():
    """弹出真实浏览器窗口完成知乎验证与扫码登录"""
    res = zhihu_auth.start_browser_login()
    app_logger.info("[ZhihuRouter] /auth/browser_login -> %s", res)
    return res


@zhihu_router.get("/auth/browser_login/status")
@account_operation
def zhihu_browser_login_status():
    """查询真实浏览器窗口登录状态"""
    res = zhihu_auth.get_browser_login_status()
    return res


@zhihu_router.post("/auth/import_cookie")
@account_operation
def zhihu_import_cookie(req: Dict[str, Any]):
    """手动导入知乎 Cookie 字符串（包含 z_c0 / d_c0）"""
    cookie_str = (req.get("cookie") or "").strip()
    zhihu_auth.cancel_login()
    ok, msg, profile = zhihu_auth.import_cookie_string(cookie_str)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    _sync_current_zhihu_to_db(profile)
    return {
        "success": True,
        "message": msg,
        "uname": (profile or {}).get("name", "知乎用户"),
        "face": (profile or {}).get("avatar_url", ""),
        "url_token": (profile or {}).get("url_token", ""),
    }


@zhihu_router.post("/auth/cancel_login")
def zhihu_cancel_login():
    zhihu_auth.cancel_login()
    return {"success": True}


@zhihu_router.post("/auth/logout")
@account_operation
def zhihu_logout():
    zhihu_auth.logout()
    return {"success": True, "message": "已退出知乎登录"}


# ==================== 2. 全局统计与一键巡检 ====================

@zhihu_router.get("/summary")
@account_operation
def zhihu_summary():
    """返回知乎各模块未读数与总条数统计"""
    counts = db.get_zhihu_summary_counts()
    counts["creators_count"] = len(db.get_zhihu_creators())
    counts["collections_count"] = len(db.get_zhihu_collections())
    return counts


@account_operation
def run_zhihu_auto_check_once(auto_archive: bool = False, scope: str = "all", scheduled: bool = False) -> Dict[str, Any]:
    """
    执行一次知乎监听源的增量巡检（支持按 scope='all'|'creators'|'collections'|'upvotes' 定向刷新）：
    - 若当前已登录且关注答主列表为空（或主动刷新答主），自动先从知乎同步关注答主列表
    """
    current = db.get_current_zhihu_account()
    if scheduled and (not current or not current.get("auto_refresh", 1) or not zhihu_auth.is_login):
        return {"success": False, "new_count": 0, "message": "账号未启用或未登录，已跳过自动刷新"}
    new_items: List[Dict[str, Any]] = []
    updated_items: List[Dict[str, Any]] = []
    errors: List[str] = []
    synced_creators_count = 0

    my_token = (zhihu_auth.user_profile or {}).get("url_token") or db.get_zhihu_setting("zhihu_upvote_token", "")
    if not my_token and zhihu_auth.is_login:
        _, prof = zhihu_auth.verify_login()
        my_token = (prof or {}).get("url_token", "")

    # 1. 巡检知乎答主
    if scope in ("all", "creators"):
        creators = db.get_zhihu_creators()
        if my_token and (not creators or scope == "creators"):
            try:
                max_follow_pages = 5 if not creators else 1
                for p in range(max_follow_pages):
                    c_page, is_end = zhihu_client.get_user_followings(my_token, offset=p * 20, limit=20)
                    for cr_item in c_page:
                        db.save_zhihu_creator(cr_item, group_name="关注答主")
                        synced_creators_count += 1
                    if is_end or not c_page:
                        break
                creators = db.get_zhihu_creators()
            except Exception as e:
                app_logger.warning("[ZhihuAutoCheck] 自动同步关注答主列表异常: %s", e)
        # 按距上次同步时间最久优先排序，单次巡检最多轮询 8 位答主，避免答主过多导致请求阻塞超时
        creators = sorted(creators, key=lambda c: int(c.get("last_sync_time") or 0))[:8]
    else:
        creators = []

    for cr in creators:
        token = cr.get("url_token")
        cr_name = cr.get("name") or token
        if not token:
            continue
        meta_map = db.get_zhihu_contents_meta_map("creator", token)
        try:
            ans_list = []
            art_list = []
            if int(cr.get("answer_count") if cr.get("answer_count") is not None else 1) > 0:
                ans_list, _ = zhihu_client.get_creator_answers(token, offset=0, limit=10)
            if int(cr.get("articles_count") if cr.get("articles_count") is not None else 1) > 0:
                art_list, _ = zhihu_client.get_creator_articles(token, offset=0, limit=10)
            combined = ans_list + art_list
            for x in combined:
                x["source_title"] = cr_name
                cid = str(x.get("content_id") or "")
                new_up_ts = int(x.get("updated_time") or x.get("created_time") or 0)
                if cid not in meta_map:
                    # 新增内容
                    x["is_read"] = 0 if len(meta_map) > 0 else 1
                    if len(meta_map) > 0:
                        new_items.append(x)
                    db.upsert_zhihu_contents([x], is_read_default=x["is_read"])
                else:
                    # 检查回答/文章是否发生后续编辑更新
                    old_up_ts = int(meta_map[cid].get("updated_time") or 0)
                    if new_up_ts > old_up_ts:
                        x["is_read"] = 0
                        updated_items.append(x)
                        db.upsert_zhihu_contents([x], is_read_default=0)

            max_c = max((int(x.get("created_time") or 0) for x in combined), default=0)
            db.update_zhihu_creator_sync_meta(token, max_c)
        except Exception as e:
            errors.append(f"答主 {token}: {e}")

    # 2. 巡检知乎收藏夹
    if scope in ("all", "collections"):
        collections = db.get_zhihu_collections()
        if my_token and not collections:
            try:
                cols = zhihu_client.get_user_collections(my_token, include_following=True)
                if cols:
                    db.save_zhihu_collections(cols)
                    collections = db.get_zhihu_collections()
            except Exception:
                pass
    else:
        collections = []
    for col in collections:
        cid = col.get("collection_id")
        col_title = col.get("title") or f"收藏夹 #{cid}"
        if not cid:
            continue
        meta_map = db.get_zhihu_contents_meta_map("collection", cid)
        try:
            items, _ = zhihu_client.get_collection_items(cid, offset=0, limit=20)
            for x in items:
                x["source_title"] = col_title
                content_id = str(x.get("content_id") or "")
                new_up_ts = int(x.get("updated_time") or x.get("created_time") or 0)
                if content_id not in meta_map:
                    x["is_read"] = 0 if len(meta_map) > 0 else 1
                    if len(meta_map) > 0:
                        new_items.append(x)
                    db.upsert_zhihu_contents([x], is_read_default=x["is_read"])
                else:
                    old_up_ts = int(meta_map[content_id].get("updated_time") or 0)
                    if new_up_ts > old_up_ts:
                        x["is_read"] = 0
                        updated_items.append(x)
                        db.upsert_zhihu_contents([x], is_read_default=0)

            if items:
                max_c = max((int(x.get("created_time") or 0) for x in items), default=0)
                db.update_zhihu_collection_sync_meta(cid, max_c)
        except Exception as e:
            errors.append(f"收藏夹 {cid}: {e}")

    # 3. 巡检我赞同的帖子
    if scope in ("all", "upvotes") and my_token:
        last_upvote_ts = int(db.get_zhihu_setting("zhihu_latest_upvote_time", "0") or 0)
        meta_map = db.get_zhihu_contents_meta_map("upvote", my_token)
        try:
            upvoted, _, _ = zhihu_client.get_member_upvoted_activities(my_token, limit=20)
            for x in upvoted:
                x["source_title"] = "我赞同的知乎动态"
                content_id = str(x.get("content_id") or "")
                new_up_ts = int(x.get("updated_time") or x.get("created_time") or 0)
                if content_id not in meta_map:
                    x["is_read"] = 0 if last_upvote_ts > 0 else 1
                    if last_upvote_ts > 0:
                        new_items.append(x)
                    db.upsert_zhihu_contents([x], is_read_default=x["is_read"])
                else:
                    old_up_ts = int(meta_map[content_id].get("updated_time") or 0)
                    if new_up_ts > old_up_ts:
                        x["is_read"] = 0
                        updated_items.append(x)
                        db.upsert_zhihu_contents([x], is_read_default=0)

            if upvoted:
                max_u = max((int(x.get("upvote_time") or 0) for x in upvoted), default=0)
                db.set_zhihu_setting("zhihu_latest_upvote_time", str(max_u))
        except Exception as e:
            errors.append(f"赞同动态 ({my_token}): {e}")

    # 若开启自动归档，只对新增或更新的非盐选/非付费内容推入归档队列
    actionable_items = [it for it in (new_items + updated_items) if it.get("paid_check_version", 0) < 2 or (not it.get("is_paid") and not it.get("is_restricted"))]
    if auto_archive and actionable_items:
        for item in actionable_items:
            try:
                _submit_archive_task(item)
            except Exception:
                pass

    total_changes = len(new_items) + len(updated_items)
    msg_parts = []
    if synced_creators_count > 0:
        msg_parts.append(f"已同步 {len(creators)} 位关注答主")
    if new_items:
        msg_parts.append(f"新增 {len(new_items)} 篇")
    if updated_items:
        msg_parts.append(f"更新 {len(updated_items)} 篇")
    change_desc = "，".join(msg_parts) if msg_parts else "暂无新增或更新"

    return {
        "success": True,
        "new_count": len(new_items),
        "updated_count": len(updated_items),
        "total_changes": total_changes,
        "errors": errors,
        "summary": db.get_zhihu_summary_counts(),
        "message": f"知乎刷新完成：{change_desc}" + (f"（部分源受限: {len(errors)}）" if errors else ""),
    }


@zhihu_router.post("/autocheck/run_now")
@account_operation
def zhihu_run_autocheck_now(req: Optional[Dict[str, Any]] = None):
    auto_arch = bool((req or {}).get("auto_archive", False))
    scope = str((req or {}).get("scope") or "all").strip()
    return run_zhihu_auto_check_once(auto_archive=auto_arch, scope=scope)


# ==================== 3. 模块一：知乎博主（答主）监听 ====================

@zhihu_router.get("/creators")
@account_operation
def list_zhihu_creators(group_name: Optional[str] = None):
    creators = db.get_zhihu_creators(group_name=group_name)
    return {"total": len(creators), "items": creators}


@zhihu_router.post("/creators/add")
@account_operation
def add_zhihu_creator(req: Dict[str, Any]):
    """通过知乎主页链接或 url_token 添加监听答主，并自动抓取首屏回答与文章建立基线"""
    input_text = (req.get("input_text") or req.get("url_token") or "").strip()
    group_name = (req.get("group_name") or "默认分组").strip()
    if not input_text:
        raise HTTPException(status_code=400, detail="请输入知乎答主主页链接或 url_token")

    token = zhihu_client.extract_url_token(input_text)
    if not token:
        raise HTTPException(status_code=400, detail="无法识别有效的知乎 url_token")

    profile = zhihu_client.get_creator_profile(token) or {
        "url_token": token,
        "name": token,
        "avatar_url": "",
        "headline": "本地订阅知乎答主",
        "answer_count": 0,
        "articles_count": 0,
        "follower_count": 0,
    }
    db.save_zhihu_creator(profile, group_name=group_name)

    # 拉取首屏回答与专栏文章建立初始基线
    fetched_items: List[Dict[str, Any]] = []
    try:
        ans, _ = zhihu_client.get_creator_answers(token, offset=0, limit=20)
        fetched_items.extend(ans)
    except Exception as e:
        print(f"[!] [ZhihuCreator] 初始拉取 {token} 回答异常: {e}", flush=True)

    try:
        arts, _ = zhihu_client.get_creator_articles(token, offset=0, limit=20)
        fetched_items.extend(arts)
    except Exception as e:
        print(f"[!] [ZhihuCreator] 初始拉取 {token} 文章异常: {e}", flush=True)

    if fetched_items:
        db.upsert_zhihu_contents(fetched_items, is_read_default=1)
        max_c = max(int(x.get("created_time") or 0) for x in fetched_items)
        db.update_zhihu_creator_sync_meta(token, max_c)

    return {
        "success": True,
        "creator": profile,
        "fetched_count": len(fetched_items),
        "message": f"已添加知乎答主「{profile.get('name', token)}」，初始同步 {len(fetched_items)} 篇内容",
    }


@zhihu_router.post("/creators/remove")
@account_operation
def remove_zhihu_creator(req: Dict[str, Any]):
    token = (req.get("url_token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="缺少 url_token")
    ok = db.delete_zhihu_creator(token)
    return {"success": ok, "url_token": token}


@zhihu_router.post("/creators/sync")
@account_operation
def sync_zhihu_creator(req: Dict[str, Any]):
    """同步指定知乎答主的内容（支持增量同步或全量多页深度抓取）"""
    token = (req.get("url_token") or "").strip()
    deep_scrape = bool(req.get("deep_scrape", False))
    max_pages = min(max(int(req.get("max_pages", 3 if deep_scrape else 1)), 1), 30)
    include_videos = bool(req.get("include_videos", False))

    if not token:
        raise HTTPException(status_code=400, detail="缺少 url_token")

    latest_known, _ = db.get_zhihu_creator_sync_meta(token)
    all_fetched: List[Dict[str, Any]] = []

    # 1. 分页抓取回答
    for p in range(max_pages):
        try:
            ans, is_end = zhihu_client.get_creator_answers(token, offset=p * 20, limit=20)
            all_fetched.extend(ans)
            if is_end or not ans or not deep_scrape:
                break
        except Exception as e:
            if p == 0:
                print(f"[!] 同步答主 {token} 回答失败: {e}", flush=True)
            break

    # 2. 分页抓取专栏文章
    for p in range(max_pages):
        try:
            arts, is_end = zhihu_client.get_creator_articles(token, offset=p * 20, limit=20)
            all_fetched.extend(arts)
            if is_end or not arts or not deep_scrape:
                break
        except Exception as e:
            if p == 0:
                print(f"[!] 同步答主 {token} 文章失败: {e}", flush=True)
            break

    # 3. 可选抓取视频
    if include_videos:
        try:
            vids, _ = zhihu_client.get_creator_videos(token, offset=0, limit=20)
            all_fetched.extend(vids)
        except Exception:
            pass

    new_cnt = 0
    if all_fetched:
        for item in all_fetched:
            c_time = int(item.get("created_time") or 0)
            if latest_known > 0 and not deep_scrape and c_time > latest_known:
                item["is_read"] = 0
                new_cnt += 1
            else:
                item["is_read"] = 1
        db.upsert_zhihu_contents(all_fetched, is_read_default=1)
        max_c = max(int(x.get("created_time") or 0) for x in all_fetched)
        db.update_zhihu_creator_sync_meta(token, max_c)
        if new_cnt > 0 and (bool(req.get("auto_archive")) or db.get_setting("auto_download_new", "false").lower() == "true"):
            for it in all_fetched:
                if it.get("is_read") == 0:
                    try:
                        _submit_archive_task(it)
                    except Exception:
                        pass

    return {
        "success": True,
        "url_token": token,
        "fetched_count": len(all_fetched),
        "new_count": new_cnt,
        "message": f"已同步 {len(all_fetched)} 篇内容" + (f"（新增未读 {new_cnt} 篇）" if new_cnt else ""),
    }


@zhihu_router.post("/creators/sync_account")
@account_operation
def sync_account_creators(req: Optional[Dict[str, Any]] = None):
    """一键从当前登录知乎账号拉取我关注的答主列表并加入博主监听"""
    url_token = ((req or {}).get("url_token") or "").strip()
    if not url_token:
        _, profile = zhihu_auth.verify_login()
        url_token = (profile or {}).get("url_token", "")
    if not url_token:
        raise HTTPException(status_code=400, detail="请先登录知乎，或传入 url_token")

    max_pages = min(max(int((req or {}).get("max_pages", 3)), 1), 10)
    all_creators = []
    for p in range(max_pages):
        try:
            creators_page, is_end = zhihu_client.get_user_followings(url_token, offset=p * 20, limit=20)
            all_creators.extend(creators_page)
            if is_end or not creators_page:
                break
        except Exception as e:
            if p == 0:
                raise HTTPException(status_code=500, detail=f"拉取关注答主失败: {e}")
            break

    for cr in all_creators:
        db.save_zhihu_creator(cr, group_name="关注答主")

    app_logger.info("[ZhihuRouter] 成功同步关注答主 %d 位", len(all_creators))
    return {
        "success": True,
        "count": len(all_creators),
        "items": db.get_zhihu_creators(),
        "message": f"已成功从知乎导入关注的 {len(all_creators)} 位答主",
    }


@zhihu_router.post("/init_account_data")
@account_operation
def init_account_data():
    """
    知乎账号登录后自动初始化（Auto-Bootstrap）：
    1. 验证个人身份并读取 url_token
    2. 自动拉取账号的收藏夹列表
    3. 自动绑定『我赞同的帖子』并抓取最近首屏赞同内容
    """
    if not zhihu_auth.is_login:
        return {"success": False, "message": "未登录知乎账号"}

    ok, profile = zhihu_auth.verify_login()
    if not ok or not profile:
        return {"success": False, "message": "校验知乎身份失败"}

    url_token = profile.get("url_token") or ""
    uname = profile.get("name", "知乎用户")
    added_creators = 0
    added_cols = 0
    added_upvotes = 0

    # 0. 自动同步我关注的答主列表
    if url_token:
        try:
            for p in range(5):
                c_page, is_end = zhihu_client.get_user_followings(url_token, offset=p * 20, limit=20)
                for cr in c_page:
                    db.save_zhihu_creator(cr, group_name="关注答主")
                    added_creators += 1
                if is_end or not c_page:
                    break
        except Exception as e:
            app_logger.warning("[ZhihuInit] 自动同步关注答主异常: %s", e)

    # 1. 自动同步收藏夹
    try:
        cols = zhihu_client.get_user_collections(url_token, include_following=True)
        if cols:
            db.save_zhihu_collections(cols)
            added_cols = len(cols)
    except Exception as e:
        app_logger.warning("[ZhihuInit] 自动同步收藏夹异常: %s", e)

    # 2. 自动绑定赞同动态并拉取首屏
    if url_token:
        db.set_zhihu_setting("zhihu_upvote_token", url_token)
        try:
            upvoted, _, _ = zhihu_client.get_member_upvoted_activities(url_token, limit=20)
            if upvoted:
                db.upsert_zhihu_contents(upvoted, is_read_default=1)
                max_u = max((int(x.get("upvote_time") or 0) for x in upvoted), default=0)
                db.set_zhihu_setting("zhihu_latest_upvote_time", str(max_u))
                added_upvotes = len(upvoted)
        except Exception as e:
            app_logger.warning("[ZhihuInit] 自动拉取赞同动态异常: %s", e)

    app_logger.info("[ZhihuInit] 账号 %s(%s) 初始化完成: %d 关注答主, %d 收藏夹, %d 赞同", uname, url_token, added_creators, added_cols, added_upvotes)
    return {
        "success": True,
        "uname": uname,
        "url_token": url_token,
        "creators_count": added_creators,
        "collections_count": added_cols,
        "upvotes_count": added_upvotes,
        "summary": db.get_zhihu_summary_counts(),
        "message": f"知乎账号「{uname}」已就绪，自动关联 {added_creators} 位答主、{added_cols} 个收藏夹与 {added_upvotes} 篇赞同内容",
    }


# ==================== 4. 模块二：知乎收藏夹监听 ====================

@zhihu_router.get("/collections")
@account_operation
def list_zhihu_collections(source_type: Optional[str] = None):
    cols = db.get_zhihu_collections(source_type=source_type)
    return {"total": len(cols), "items": cols}


@zhihu_router.post("/collections/sync_account")
@account_operation
def sync_account_collections(req: Optional[Dict[str, Any]] = None):
    """一键从当前登录知乎账号（或指定 url_token）同步我创建的与我关注的收藏夹列表"""
    url_token = ((req or {}).get("url_token") or "").strip()
    if not url_token:
        _, profile = zhihu_auth.verify_login()
        url_token = (profile or {}).get("url_token", "")
    if not url_token:
        raise HTTPException(status_code=400, detail="请先扫码登录知乎，或输入用户 url_token")

    cols = zhihu_client.get_user_collections(url_token, include_following=True)
    if cols:
        db.save_zhihu_collections(cols)
    return {
        "success": True,
        "count": len(cols),
        "items": db.get_zhihu_collections(),
        "message": f"成功同步账号「{url_token}」下的 {len(cols)} 个收藏夹",
    }


@zhihu_router.post("/collections/add")
@account_operation
def add_zhihu_collection(req: Dict[str, Any]):
    """通过收藏夹链接或 ID 添加监听收藏夹并抓取第 1 页条目"""
    input_text = (req.get("input_text") or req.get("collection_id") or "").strip()
    if not input_text:
        raise HTTPException(status_code=400, detail="请输入知乎收藏夹链接或 ID")

    cid = zhihu_client.extract_collection_id(input_text)
    if not cid:
        raise HTTPException(status_code=400, detail="无法识别有效的知乎收藏夹 ID")

    detail = zhihu_client.get_collection_detail(cid) or {
        "collection_id": cid,
        "title": f"知乎收藏夹 #{cid}",
        "description": "",
        "creator_name": "知乎用户",
        "creator_token": "",
        "item_count": 0,
        "follower_count": 0,
        "source_type": "custom",
    }
    db.save_zhihu_collections([detail])

    items: List[Dict[str, Any]] = []
    try:
        items, _ = zhihu_client.get_collection_items(cid, offset=0, limit=20)
        if items:
            db.upsert_zhihu_contents(items, is_read_default=1)
            max_c = max(int(x.get("created_time") or 0) for x in items)
            db.update_zhihu_collection_sync_meta(cid, max_c)
    except Exception as e:
        print(f"[!] 初始抓取收藏夹 {cid} 条目异常: {e}", flush=True)

    return {
        "success": True,
        "collection": detail,
        "fetched_count": len(items),
        "message": f"已添加收藏夹「{detail.get('title')}」，同步 {len(items)} 条内容",
    }


@zhihu_router.post("/collections/remove")
@account_operation
def remove_zhihu_collection(req: Dict[str, Any]):
    cid = str(req.get("collection_id") or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="缺少 collection_id")
    ok = db.delete_zhihu_collection(cid)
    return {"success": ok, "collection_id": cid}


@zhihu_router.post("/collections/sync_items")
@account_operation
def sync_zhihu_collection_items(req: Dict[str, Any]):
    """增量或深度分页抓取指定知乎收藏夹内的内容"""
    cid = str(req.get("collection_id") or "").strip()
    deep_scrape = bool(req.get("deep_scrape", False))
    max_pages = min(max(int(req.get("max_pages", 5 if deep_scrape else 1)), 1), 50)
    if not cid:
        raise HTTPException(status_code=400, detail="缺少 collection_id")

    existing_ids = db.get_zhihu_collection_existing_ids(cid)
    all_items: List[Dict[str, Any]] = []

    for p in range(max_pages):
        try:
            batch, is_end = zhihu_client.get_collection_items(cid, offset=p * 20, limit=20)
            all_items.extend(batch)
            if is_end or not batch or not deep_scrape:
                break
        except Exception as e:
            if p == 0:
                raise HTTPException(status_code=500, detail=f"抓取收藏夹 {cid} 失败: {e}")
            break

    new_cnt = 0
    if all_items:
        for item in all_items:
            item_cid = str(item.get("content_id") or "")
            # 若该条目未在本地此收藏夹中出现过，且非首次初始化拉取，判定为新增未读收藏
            if len(existing_ids) > 0 and not deep_scrape and item_cid not in existing_ids:
                item["is_read"] = 0
                new_cnt += 1
            else:
                item["is_read"] = 1
        db.upsert_zhihu_contents(all_items, is_read_default=1)
        max_c = max((int(x.get("created_time") or 0) for x in all_items), default=0)
        db.update_zhihu_collection_sync_meta(cid, max_c)
        if new_cnt > 0 and (bool(req.get("auto_archive")) or db.get_setting("auto_download_new", "false").lower() == "true"):
            for it in all_items:
                if it.get("is_read") == 0:
                    try:
                        _submit_archive_task(it)
                    except Exception:
                        pass

    return {
        "success": True,
        "collection_id": cid,
        "fetched_count": len(all_items),
        "new_count": new_cnt,
        "message": f"已同步收藏夹 {len(all_items)} 条内容" + (f"（新增 {new_cnt} 条）" if new_cnt else ""),
    }


# ==================== 5. 模块三：自己点赞（赞同）的帖子监听 ====================

@zhihu_router.get("/upvotes/status")
@account_operation
def get_upvotes_monitor_status():
    """获取当前赞同监听绑定的用户与统计"""
    profile_token = (zhihu_auth.user_profile or {}).get("url_token", "")
    bound_token = db.get_zhihu_setting("zhihu_upvote_token", "") or profile_token
    latest_ts = int(db.get_zhihu_setting("zhihu_latest_upvote_time", "0") or 0)
    summary = db.get_zhihu_summary_counts()
    return {
        "url_token": bound_token,
        "latest_upvote_time": latest_ts,
        "total_cached": summary.get("upvote_total", 0),
        "unread_count": summary.get("upvote_unread", 0),
    }


@zhihu_router.post("/upvotes/sync")
@account_operation
def sync_zhihu_upvotes(req: Optional[Dict[str, Any]] = None):
    """
    拉取当前登录账号（或指定 url_token）赞同过的回答/文章动态流
    支持增量同步 (max_pages=1) 与深度历史回溯 (deep_scrape=True, max_pages=10)
    """
    payload = req or {}
    input_token = (payload.get("url_token") or "").strip()
    if input_token:
        token = zhihu_client.extract_url_token(input_token)
        if token != db.get_zhihu_setting("zhihu_upvote_token", ""):
            db.set_zhihu_setting("zhihu_latest_upvote_time", "0")
        db.set_zhihu_setting("zhihu_upvote_token", token)
    else:
        _, profile = zhihu_auth.verify_login()
        token = (profile or {}).get("url_token") or db.get_zhihu_setting("zhihu_upvote_token", "")

    if not token:
        raise HTTPException(
            status_code=400,
            detail="请先登录知乎，或在输入框中填入你的知乎个人主页链接 / url_token 以绑定赞同监听",
        )

    deep_scrape = bool(payload.get("deep_scrape", False))
    max_pages = min(max(int(payload.get("max_pages", 5 if deep_scrape else 1)), 1), 30)
    last_upvote_ts = int(db.get_zhihu_setting("zhihu_latest_upvote_time", "0") or 0)

    all_upvoted: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    for p in range(max_pages):
        try:
            batch, next_cursor, is_end = zhihu_client.get_member_upvoted_activities(
                token, after_id=cursor, limit=20
            )
            all_upvoted.extend(batch)
            cursor = next_cursor
            if is_end or not cursor or not deep_scrape:
                break
        except Exception as e:
            if p == 0:
                raise HTTPException(status_code=500, detail=f"拉取用户 {token} 赞同动态失败: {e}")
            break

    new_cnt = 0
    if all_upvoted:
        for item in all_upvoted:
            u_time = int(item.get("upvote_time") or 0)
            if last_upvote_ts > 0 and not deep_scrape and u_time > last_upvote_ts:
                item["is_read"] = 0
                new_cnt += 1
            else:
                item["is_read"] = 1
        db.upsert_zhihu_contents(all_upvoted, is_read_default=1)
        max_u = max(int(x.get("upvote_time") or 0) for x in all_upvoted)
        if new_cnt > 0 and (bool(payload.get("auto_archive")) or db.get_setting("auto_download_new", "false").lower() == "true"):
            for it in all_upvoted:
                if it.get("is_read") == 0:
                    try:
                        _submit_archive_task(it)
                    except Exception:
                        pass
        if max_u > last_upvote_ts:
            db.set_zhihu_setting("zhihu_latest_upvote_time", str(max_u))

    return {
        "success": True,
        "url_token": token,
        "fetched_count": len(all_upvoted),
        "new_count": new_cnt,
        "message": f"已从「{token}」的动态流提取 {len(all_upvoted)} 篇赞同内容" + (f"（新增 {new_cnt} 篇）" if new_cnt else ""),
    }


# ==================== 6. 模块四：知乎内容查询、阅读器详情、单链解析与本地归档 ====================

@zhihu_router.get("/contents")
@account_operation
def get_zhihu_contents_list(
    source_type: str = "all",
    source_id: str = "all",
    content_type: str = "all",
    only_unread: bool = False,
    only_archived: bool = False,
    search: str = "",
    limit: int = 300,
    offset: int = 0,
):
    items = db.query_zhihu_contents(
        source_type=source_type,
        source_id=source_id,
        content_type=content_type,
        only_unread=only_unread,
        only_archived=only_archived,
        search_kw=search,
        limit=limit,
        offset=offset,
    )
    return {
        "total": len(items),
        "items": [_present_content(item) for item in items],
        "summary": db.get_zhihu_summary_counts(),
    }


@zhihu_router.get("/content/detail")
@account_operation
def get_zhihu_content_full_detail(content_id: str):
    """获取单篇知乎回答/文章详情，供右侧阅读器沉浸式预览；若本地正文不完整则自动在线拉取补全"""
    item = db.get_zhihu_content_detail(content_id)
    if not item:
        raise HTTPException(status_code=404, detail="未找到该知乎内容记录")

    item = _refresh_content_access(item)
    if len(item.get("content_html") or "") < 40 and not item.get("is_paid"):
        # Force a full-detail check before replacing a short cached excerpt.
        item = _refresh_content_access({**item, "paid_check_version": 0})

    # 阅读后自动标为已读
    if item.get("is_read") == 0:
        db.mark_zhihu_contents_read(content_id=content_id)
        item["is_read"] = 1

    return {"success": True, "item": item}


@zhihu_router.post("/contents/mark_read")
@account_operation
def mark_zhihu_read(req: Dict[str, Any]):
    cid = req.get("content_id")
    s_type = req.get("source_type")
    s_id = req.get("source_id")
    affected = db.mark_zhihu_contents_read(content_id=cid, source_type=s_type, source_id=s_id)
    return {
        "success": True,
        "affected": affected,
        "summary": db.get_zhihu_summary_counts(),
    }


@zhihu_router.post("/parse_url")
@account_operation
def parse_zhihu_url(req: Dict[str, Any]):
    """解析单篇回答、专栏文章或整个问题下的高赞回答并存入 single 模块"""
    url = (req.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="请输入知乎回答、专栏文章或问题链接")

    items = zhihu_client.fetch_single_content_by_url(url)
    if not items:
        raise HTTPException(status_code=404, detail="未能从该链接提取到知乎回答或专栏文章，请检查链接格式或登录状态")

    db.upsert_zhihu_contents(items, is_read_default=1)
    return {
        "success": True,
        "count": len(items),
        "items": items,
        "message": f"成功解析 {len(items)} 篇知乎图文内容",
    }


@zhihu_router.post("/archive")
@account_operation
def archive_zhihu_items(req: Dict[str, Any]):
    """将单篇或多篇知乎内容提交至后台 Markdown + 本地图片归档队列"""
    content_ids: List[str] = []
    if req.get("content_id"):
        content_ids.append(str(req["content_id"]))
    if isinstance(req.get("content_ids"), list):
        content_ids.extend(str(x) for x in req["content_ids"] if x)

    if not content_ids:
        raise HTTPException(status_code=400, detail="未选择要归档的知乎内容")

    submitted = []
    blocked_count = 0
    for cid in content_ids:
        item = db.get_zhihu_content_detail(cid)
        if item:
            item = _refresh_content_access(item)
            if item.get("is_paid") or item.get("is_restricted"):
                blocked_count += 1
                continue
            task = _submit_archive_task(item)
            submitted.append(task)

    if blocked_count > 0 and len(submitted) == 0:
        raise HTTPException(
            status_code=403,
            detail="所选条目包含知乎盐选专栏或付费内容，已被系统合规防护规则拦截，严格禁止离线归档。"
        )

    msg = f"已将 {len(submitted)} 篇知乎图文加入本地 Markdown+配图归档队列"
    if blocked_count > 0:
        msg += f"（已自动拦截跳过 {blocked_count} 篇盐选/付费受限内容）"

    return {
        "success": True,
        "count": len(submitted),
        "blocked_count": blocked_count,
        "tasks": submitted,
        "message": msg,
    }


@zhihu_router.get("/archive/tasks")
@account_operation
def list_zhihu_archive_tasks():
    tasks = get_zhihu_archive_tasks()
    active = sum(1 for t in tasks if t.get("status") in ("pending", "downloading"))
    return {"tasks": tasks, "active_count": active}
