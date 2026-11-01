"""多线程下载队列与全局任务管理器 (支持结构化归档、实时速度、自动重试与状态持久化)."""

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Dict, List, Optional

from builder.auth import BiliAuth
from downloader import DownloadCancelledError, MediaDownloader, RestrictedVideoError
from utils import db


def build_task_key(
    video_info: dict,
    quality: str = "1080P",
    audio_only: bool = True,
    audio_quality: str = "30280",
) -> str:
    """构建下载请求唯一去重键：包含内容 ID (bvid)、分P (cid/part_page)、音视频类型及目标质量."""
    raw_bvid = str(video_info.get("bvid") or "").strip()
    real_bvid = str(video_info.get("real_bvid") or raw_bvid.split("_P")[0]).strip()
    cid = video_info.get("cid")
    part_page = video_info.get("part_page") or video_info.get("page")
    if not part_page and "_P" in raw_bvid:
        part_page = raw_bvid.split("_P", 1)[1]
    part_id = f"cid{cid}" if cid else f"p{part_page or 1}"
    media_type = "audio" if audio_only else "video"
    aq_norm = str(audio_quality or "30280").strip().upper()
    if audio_only:
        q_norm = aq_norm
    else:
        vq_norm = str(quality or "1080P").strip().upper()
        q_norm = f"{vq_norm}_{aq_norm}"
    return f"{real_bvid}:{part_id}:{media_type}:{q_norm}"


class DownloadTask:
    def __init__(
        self,
        video_info: dict,
        quality: str = "1080P",
        audio_only: bool = True,
        audio_quality: str = "30280",
        block_paid: bool = True,
        account_id: str = "",
        account_name: str = "",
        fallback_auth=None,
    ):
        self.bvid = video_info.get("bvid", "")
        self.task_key = build_task_key(
            video_info,
            quality=quality,
            audio_only=audio_only,
            audio_quality=audio_quality,
        )
        self.title = (video_info.get("title") or video_info.get("视频名字") or self.bvid).strip()
        self.author = (video_info.get("author") or video_info.get("UP名字") or "未知UP主").strip()
        self.pic = video_info.get("pic") or video_info.get("封面") or ""
        self.account_id = str(account_id or video_info.get("account_id") or "").strip()
        self.account_name = (
            account_name
            or video_info.get("account_name")
            or (db.get_current_account() or {}).get("uname")
            or "B站账号"
        )
        self._fallback_auth = fallback_auth
        self.video_info = video_info
        self.quality = quality
        self.audio_only = audio_only
        self.audio_quality = audio_quality
        self.block_paid = block_paid
        self.cancel_event = threading.Event()
        self.future: Optional[Future] = None

        self.status = "pending"  # pending, downloading, success, failed, cancelled, restricted
        self.progress = 0.0
        self.speed = "0 KB/s"
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.file_path = ""
        self.error = None
        self.retries = 0
        self.max_retries = 3
        self.created_at = time.time()
        self.updated_at = time.time()

    def is_worker_running(self) -> bool:
        """判断当前任务的后台工作项 Future 是否仍在运行中."""
        return self.future is not None and not self.future.done()

    def to_dict(self) -> dict:
        vinfo = self.video_info or {}
        g_type = "folder" if (vinfo.get("fav_folder_id") or vinfo.get("media_id")) else "author"
        g_name = vinfo.get("fav_folder_title") or vinfo.get("folder_name") or self.author or "未分类UP主"
        stopping = self.status == "cancelled" and self.is_worker_running()
        return {
            "bvid": self.bvid,
            "task_key": self.task_key,
            "platform": "bili",
            "account_id": self.account_id,
            "account_name": getattr(self, "account_name", "") or "B站账号",
            "group_type": g_type,
            "group_name": g_name,
            "title": self.title,
            "author": self.author,
            "pic": self.pic,
            "quality": self.quality,
            "audio_only": self.audio_only,
            "audio_quality": self.audio_quality,
            "status": self.status,
            "is_stopping": stopping,
            "can_retry": self.status in ("failed", "cancelled") and not self.is_worker_running(),
            "progress": round(self.progress, 1),
            "speed": "正在停止..." if stopping else self.speed,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "file_path": self.file_path,
            "error": self.error,
            "retries": self.retries,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class DownloadManager:
    def __init__(self, max_workers: int = 3):
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="bili-downloader")
        self.tasks: Dict[str, DownloadTask] = {}
        self.cleared_bvids: set = set()
        self.lock = threading.Lock()
        self.last_retry_error: str = ""

    def set_max_workers(self, new_workers: int):
        """动态修改最大并发下载工作线程数."""
        with self.lock:
            new_workers = max(1, min(16, int(new_workers)))
            if new_workers != self.max_workers:
                self.max_workers = new_workers
                try:
                    self.executor._max_workers = new_workers
                except Exception:
                    pass
                print(f"[*] 下载管理器并发线程数已调整为: {new_workers}", flush=True)

    @staticmethod
    def _resolve_account_meta(auth, video_info: dict) -> tuple:
        """从任务入参、传入的 Auth 对象或当前激活账号提取稳定的账号 ID (mid) 与名称."""
        explicit_id = str(video_info.get("account_id") or video_info.get("account_mid") or "").strip()
        auth_mid = str(getattr(auth, "mid", "") or "").strip()
        cur_acc = db.get_current_account()
        cur_mid = str((cur_acc or {}).get("mid") or "").strip()

        acc_id = explicit_id or auth_mid or cur_mid
        acc_name = str(video_info.get("account_name") or "").strip()
        if acc_id:
            acc_row = db.get_account_by_mid(acc_id)
            if acc_row:
                acc_name = acc_row.get("uname") or acc_name or f"UID:{acc_id}"
            elif cur_acc and str(cur_acc.get("mid")) == acc_id:
                acc_name = cur_acc.get("uname") or acc_name or f"UID:{acc_id}"
            else:
                acc_name = acc_name or f"UID:{acc_id}"
        else:
            acc_name = acc_name or (cur_acc or {}).get("uname") or "B站账号"
        return acc_id, acc_name

    @staticmethod
    def _resolve_task_auth(task: DownloadTask, fallback_auth=None):
        """按任务绑定的稳定账号 ID (account_id) 获取凭据；若原所属账号已被移除或禁用则拒绝并使用错误提示."""
        if task.account_id:
            acc = db.get_account_by_mid(task.account_id)
            if not acc or not acc.get("cookie"):
                raise ValueError(
                    f"原任务所属账号 ({task.account_name or task.account_id}) 已不存在，请重新选择账号后再下载"
                )
            if int(acc.get("is_enabled", 1)) == 0:
                raise ValueError(
                    f"原任务所属账号【{acc.get('uname') or task.account_name or task.account_id}】已禁用，请启用或重新选择账号"
                )
            task_auth = BiliAuth.from_cookie(acc["cookie"])
            task_auth.refresh_token = acc.get("refresh_token") or ""
            task.account_name = acc.get("uname") or task.account_name
            return task_auth

        return task._fallback_auth if task._fallback_auth is not None else fallback_auth

    def add_task(
        self,
        auth,
        video_info: dict,
        quality: str = "1080P",
        audio_only: bool = True,
        audio_quality: str = "30280",
        block_paid: Optional[bool] = None,
        download_dir: Optional[str] = None,
    ) -> DownloadTask:
        bvid = video_info.get("bvid")
        if not bvid:
            raise ValueError("缺少 bvid")

        if block_paid is None:
            block_paid = db.get_setting("block_paid_videos", "true").lower() == "true"

        task_key = build_task_key(
            video_info,
            quality=quality,
            audio_only=audio_only,
            audio_quality=audio_quality,
        )
        acc_id, acc_name = self._resolve_account_meta(auth, video_info)
        target_dir = download_dir or db.get_download_dir()

        with self.lock:
            existing = self.tasks.get(task_key)
            # 相同去重键（同内容ID、同分P、同音视频类型及同质量）正在运行或尚在停止中时直接复用
            if existing and (existing.status in ["pending", "downloading"] or existing.is_worker_running()):
                return existing

            task = DownloadTask(
                video_info,
                quality=quality,
                audio_only=audio_only,
                audio_quality=audio_quality,
                block_paid=block_paid,
                account_id=acc_id,
                account_name=acc_name,
                fallback_auth=auth if not acc_id else None,
            )
            task_auth = self._resolve_task_auth(task, fallback_auth=auth)
            self.cleared_bvids.discard(bvid)
            self.cleared_bvids.discard(task_key)
            self.tasks[task_key] = task

            real_bvid = video_info.get("real_bvid") or bvid.split("_P")[0]
            db.update_download_record(real_bvid, "downloading")
            run_cancel = task.cancel_event
            task.future = self.executor.submit(self._worker_run, task_auth, task, target_dir, run_cancel)
            return task

    def add_batch_tasks(
        self,
        auth,
        videos: List[dict],
        quality: str = "1080P",
        audio_only: bool = True,
        audio_quality: str = "30280",
        block_paid: Optional[bool] = None,
        download_dir: Optional[str] = None,
    ) -> List[DownloadTask]:
        added = []
        target_dir = download_dir or db.get_download_dir()
        if block_paid is None:
            block_paid = db.get_setting("block_paid_videos", "true").lower() == "true"

        for v in videos:
            bvid = v.get("bvid")
            if not bvid:
                continue
            if block_paid and (v.get("is_restricted") or v.get("restricted") or v.get("download_status") == "restricted"):
                real_bvid = v.get("real_bvid") or bvid.split("_P")[0]
                reason = v.get("restrict_reason") or "付费/充电受限"
                db.mark_video_restricted(real_bvid, reason)
                continue

            task_key = build_task_key(
                v,
                quality=quality,
                audio_only=audio_only,
                audio_quality=audio_quality,
            )
            acc_id, acc_name = self._resolve_account_meta(auth, v)

            with self.lock:
                existing = self.tasks.get(task_key)
                if existing and (
                    existing.status in ["pending", "downloading", "success", "restricted"]
                    or existing.is_worker_running()
                ):
                    continue
                task = DownloadTask(
                    v,
                    quality=quality,
                    audio_only=audio_only,
                    audio_quality=audio_quality,
                    block_paid=block_paid,
                    account_id=acc_id,
                    account_name=acc_name,
                    fallback_auth=auth if not acc_id else None,
                )
                try:
                    task_auth = self._resolve_task_auth(task, fallback_auth=auth)
                except ValueError as e:
                    task.status = "failed"
                    task.speed = "账号不可用"
                    task.error = str(e)
                    self.tasks[task_key] = task
                    continue

                self.cleared_bvids.discard(bvid)
                self.cleared_bvids.discard(task_key)
                self.tasks[task_key] = task
                added.append(task)

                real_bvid = v.get("real_bvid") or bvid.split("_P")[0]
                db.update_download_record(real_bvid, "downloading")
                run_cancel = task.cancel_event
                task.future = self.executor.submit(self._worker_run, task_auth, task, target_dir, run_cancel)

        return added

    def _worker_run(
        self,
        auth,
        task: DownloadTask,
        download_dir: str,
        cancel_event: Optional[threading.Event] = None,
    ):
        bvid = task.bvid
        real_bvid = task.video_info.get("real_bvid") or bvid.split("_P")[0]
        run_cancel = cancel_event or task.cancel_event

        try:
            run_auth = self._resolve_task_auth(task, fallback_auth=auth)
        except ValueError as acc_err:
            with self.lock:
                task.status = "failed"
                task.speed = "账号不可用"
                task.error = str(acc_err)
                task.updated_at = time.time()
            db.update_download_record(real_bvid, "failed")
            return

        downloader = MediaDownloader(auth=run_auth, download_dir=download_dir)

        def on_progress(percent: float, speed_str: str, cur_bytes: int, tot_bytes: int):
            if run_cancel.is_set():
                return
            with self.lock:
                task.progress = percent
                task.speed = speed_str
                task.downloaded_bytes = cur_bytes
                task.total_bytes = tot_bytes
                task.updated_at = time.time()

        for attempt in range(task.max_retries):
            if run_cancel.is_set():
                with self.lock:
                    task.status = "cancelled"
                    task.speed = "已取消"
                    task.error = "用户取消"
                    task.updated_at = time.time()
                db.update_download_record(real_bvid, "none")
                return

            with self.lock:
                task.status = "downloading"
                task.retries = attempt
                task.error = None

            try:
                print(f"[*] [队列调度] 开始下载 [{task.title}] (BV: {bvid}, 尝试: {attempt + 1}/{task.max_retries})...", flush=True)
                saved_path = downloader.download(
                    task.video_info,
                    quality=task.quality,
                    audio_only=task.audio_only,
                    audio_quality=task.audio_quality,
                    block_paid=task.block_paid,
                    progress_callback=on_progress,
                    cancel_event=run_cancel,
                )
                with self.lock:
                    task.status = "success"
                    task.progress = 100.0
                    task.speed = "已完成"
                    task.file_path = saved_path
                    task.updated_at = time.time()

                # 落盘持久化
                db.update_download_record(real_bvid, "success", saved_path)
                print(f"[+] [队列调度] 下载完成: {task.title} -> {saved_path}", flush=True)
                return

            except RestrictedVideoError as re_err:
                print(f"[!] [受限拦截] 跳过受限视频: {task.title} ({re_err})", flush=True)
                with self.lock:
                    task.status = "restricted"
                    task.speed = re_err.reason
                    task.error = str(re_err)
                    task.updated_at = time.time()
                db.mark_video_restricted(real_bvid, re_err.reason)
                return

            except DownloadCancelledError as ce:
                print(f"[*] [队列调度] 下载已取消: {task.title} ({ce})", flush=True)
                with self.lock:
                    task.status = "cancelled"
                    task.speed = "已取消"
                    task.error = "用户取消"
                    task.updated_at = time.time()
                db.update_download_record(real_bvid, "none")
                return

            except Exception as e:
                if run_cancel.is_set():
                    with self.lock:
                        task.status = "cancelled"
                        task.speed = "已取消"
                        task.error = "用户取消"
                        task.updated_at = time.time()
                    db.update_download_record(real_bvid, "none")
                    return

                err_msg = str(e)
                print(f"[-] [队列调度] 下载失败 (尝试 {attempt + 1}): {task.title} - {err_msg}", flush=True)
                with self.lock:
                    task.error = err_msg
                    task.updated_at = time.time()

                if attempt < task.max_retries - 1:
                    if run_cancel.wait(2.0):
                        with self.lock:
                            task.status = "cancelled"
                            task.speed = "已取消"
                            task.error = "用户取消"
                            task.updated_at = time.time()
                        db.update_download_record(real_bvid, "none")
                        return
                else:
                    with self.lock:
                        task.status = "failed"
                        task.speed = "失败"
                    db.update_download_record(real_bvid, "failed")

    def _find_task_locked(self, key_or_bvid: str, prefer_statuses: Optional[set] = None) -> Optional[DownloadTask]:
        if key_or_bvid in self.tasks:
            return self.tasks[key_or_bvid]
        candidates = [t for t in self.tasks.values() if t.bvid == key_or_bvid]
        if not candidates:
            return None
        if prefer_statuses:
            candidates.sort(key=lambda t: (t.status in prefer_statuses, t.updated_at), reverse=True)
        else:
            candidates.sort(key=lambda t: t.updated_at, reverse=True)
        return candidates[0]

    def retry_task(self, auth=None, bvid: str = "", download_dir: Optional[str] = None) -> bool:
        target_dir = download_dir or db.get_download_dir()
        with self.lock:
            self.last_retry_error = ""
            task = self._find_task_locked(bvid, prefer_statuses={"failed", "cancelled"})
            if not task:
                persisted = db.get_all_download_status()
                info = persisted.get(bvid)
                if info and info.get("status") == "interrupted":
                    v_info = {
                        "bvid": bvid,
                        "title": info.get("title") or bvid,
                        "author": info.get("author") or "未知UP主",
                        "pic": info.get("pic") or "",
                        "up_mid": info.get("up_mid") or 0,
                    }
                else:
                    v_info = None
                if v_info is None:
                    return False
        if not task and v_info is not None:
            def_vq = db.get_setting("default_video_quality", "1080P")
            def_aq = db.get_setting("default_audio_quality", "30280")
            self.add_task(auth, v_info, quality=def_vq, audio_only=True, audio_quality=def_aq, download_dir=target_dir)
            return True

        with self.lock:
            # 旧任务尚未结束前严格禁止重试，防止新旧两个工作项并发执行
            if task.status in ("pending", "downloading") or task.is_worker_running():
                if task.is_worker_running():
                    self.last_retry_error = "旧下载任务正在停止中，请等待其结束后再重试"
                return False

            try:
                task_auth = self._resolve_task_auth(task, fallback_auth=auth if not task.account_id else None)
            except ValueError as acc_err:
                err_msg = str(acc_err)
                task.status = "failed"
                task.speed = "账号不可用"
                task.error = err_msg
                task.updated_at = time.time()
                self.last_retry_error = err_msg
                return False

            self.cleared_bvids.discard(task.bvid)
            self.cleared_bvids.discard(task.task_key)
            # 旧任务已结束，创建全新的取消信号 Event，避免与旧工作项共享
            new_cancel = threading.Event()
            task.cancel_event = new_cancel
            task.status = "pending"
            task.progress = 0.0
            task.speed = "等待重试"
            task.error = None
            task.retries = 0
            task.updated_at = time.time()

            real_bvid = task.video_info.get("real_bvid") or task.bvid.split("_P")[0]
            db.update_download_record(real_bvid, "downloading")
            task.future = self.executor.submit(self._worker_run, task_auth, task, target_dir, new_cancel)
            return True

    def retry_all_failed(
        self,
        auth=None,
        download_dir: Optional[str] = None,
        bvids: Optional[List[str]] = None,
        include_cancelled: bool = False,
        only_cancelled: bool = False,
    ) -> int:
        retried = 0
        if only_cancelled:
            allowed_statuses = {"cancelled"}
        elif include_cancelled:
            allowed_statuses = {"failed", "cancelled"}
        else:
            allowed_statuses = {"failed"}
        bvid_filter = set(bvids) if bvids is not None else None

        with self.lock:
            target_tasks = [
                t
                for t in self.tasks.values()
                if t.status in allowed_statuses
                and not t.is_worker_running()
                and (bvid_filter is None or t.task_key in bvid_filter or t.bvid in bvid_filter)
            ]

        for t in target_tasks:
            if self.retry_task(auth, t.task_key, download_dir):
                retried += 1
        return retried

    def clear_completed(
        self,
        bvids: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
    ) -> int:
        allowed_statuses = set(statuses or ["success"])
        bvid_filter = set(bvids) if bvids is not None else None
        removed_set = set()
        with self.lock:
            to_remove = [
                t_key
                for t_key, t in self.tasks.items()
                if t.status in allowed_statuses
                and (bvid_filter is None or t_key in bvid_filter or t.bvid in bvid_filter)
            ]
            for t_key in to_remove:
                t_obj = self.tasks.pop(t_key, None)
                self.cleared_bvids.add(t_key)
                if t_obj:
                    self.cleared_bvids.add(t_obj.bvid)
                removed_set.add(t_key)

        if "success" in allowed_statuses:
            persisted = db.get_all_download_status()
            with self.lock:
                for bvid, info in persisted.items():
                    if info.get("status") == "success" and bvid not in self.cleared_bvids:
                        if bvid_filter is None or bvid in bvid_filter:
                            self.cleared_bvids.add(bvid)
                            removed_set.add(bvid)
        return len(removed_set)

    def cancel_task(self, bvid: str) -> bool:
        cancelled_any = False
        with self.lock:
            if bvid in self.tasks:
                targets = [self.tasks[bvid]]
            else:
                targets = [t for t in self.tasks.values() if t.bvid == bvid]

            for task in targets:
                if task.status in ["pending", "downloading"]:
                    task.cancel_event.set()
                    task.status = "cancelled"
                    task.speed = "已取消"
                    task.error = "用户取消"
                    task.updated_at = time.time()
                    real_bvid = task.video_info.get("real_bvid") or task.bvid.split("_P")[0]
                    db.update_download_record(real_bvid, "none")
                    cancelled_any = True
        return cancelled_any

    def get_all_tasks(self) -> List[dict]:
        with self.lock:
            # 内存活跃任务按更新时间倒序
            task_list = [t.to_dict() for t in self.tasks.values()]

        # 同时补充 SQLite 中已记录为成功或启动时中断但当前内存任务字典中没有的项目
        persisted = db.get_all_download_status()
        memory_bvids = {t["bvid"] for t in task_list}

        for bvid, info in persisted.items():
            st = info.get("status")
            if bvid not in memory_bvids and bvid not in self.cleared_bvids and st in ("success", "interrupted"):
                is_interrupted = st == "interrupted"
                task_list.append({
                    "bvid": bvid,
                    "task_key": bvid,
                    "platform": "bili",
                    "title": info.get("title") or bvid,
                    "author": info.get("author") or ("上次未完成" if is_interrupted else "历史已下载"),
                    "pic": info.get("pic") or "",
                    "quality": "默认",
                    "audio_only": info.get("file_path", "").endswith(".m4a"),
                    "status": "failed" if is_interrupted else "success",
                    "is_stopping": False,
                    "can_retry": is_interrupted,
                    "progress": 0.0 if is_interrupted else 100.0,
                    "speed": "已中断，可重新下载" if is_interrupted else "本地已保存",
                    "downloaded_bytes": 0,
                    "total_bytes": 0,
                    "file_path": info.get("file_path", ""),
                    "error": "已中断，可重新下载" if is_interrupted else None,
                    "retries": 0,
                    "max_retries": 3,
                    "created_at": 0,
                    "updated_at": info.get("updated_at") or 0,
                })

        task_list.sort(key=lambda x: x["updated_at"], reverse=True)
        return task_list

    def get_active_count(self) -> int:
        with self.lock:
            return sum(1 for t in self.tasks.values() if t.status in ["pending", "downloading"])


# 全局单例管理器
download_manager = DownloadManager(max_workers=3)
