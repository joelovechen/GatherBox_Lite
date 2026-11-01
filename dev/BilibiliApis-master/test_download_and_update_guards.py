import io
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import utils.db as db
import downloader
import download_manager
import server


class TestDownloadAndUpdateGuards(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.orig_db_file = db.DB_FILE
        db.set_data_dir(str(self.root))
        db.init_db()

    def tearDown(self):
        db.DB_FILE = self.orig_db_file
        self.temp_dir.cleanup()

    def test_1_cancel_and_immediate_retry_race_guarded(self):
        """取消后立即重试：旧工作项结束前禁止重试，结束后创建新取消信号，不会并发跑两个工作项。"""
        mgr = download_manager.DownloadManager(max_workers=2)
        worker_entered = threading.Event()
        allow_worker_exit = threading.Event()
        active_workers = 0
        max_concurrent_workers = 0
        observed_events = []
        lock = threading.Lock()

        def fake_download(dl_self, video_info, quality="1080P", audio_only=False, audio_quality="30280", block_paid=True, progress_callback=None, cancel_event=None):
            nonlocal active_workers, max_concurrent_workers
            with lock:
                active_workers += 1
                max_concurrent_workers = max(max_concurrent_workers, active_workers)
                observed_events.append(cancel_event)
            worker_entered.set()
            try:
                if len(observed_events) == 1:
                    allow_worker_exit.wait(timeout=5.0)
                    if cancel_event and cancel_event.is_set():
                        raise downloader.DownloadCancelledError("用户已取消下载")
                out_file = self.root / "BV1race.m4a"
                out_file.write_bytes(b"valid-audio-data")
                return str(out_file)
            finally:
                with lock:
                    active_workers -= 1

        auth_mock = MagicMock()
        auth_mock.is_login = True
        auth_mock.mid = ""

        with patch.object(downloader.MediaDownloader, "download", new=fake_download):
            vinfo = {"bvid": "BV1race", "视频名字": "并发测试", "UP名字": "测试UP"}
            task = mgr.add_task(auth_mock, vinfo, quality="1080P", audio_only=True, download_dir=str(self.root))
            self.assertTrue(worker_entered.wait(timeout=3.0))

            # 取消任务，但此时旧线程尚未退出
            self.assertTrue(mgr.cancel_task("BV1race"))
            self.assertIsNotNone(task)
            self.assertTrue(task.is_worker_running())
            self.assertFalse(task.to_dict()["can_retry"])

            # 立即重试必须被拒绝，不会启动第二个工作线程
            self.assertFalse(mgr.retry_task(auth_mock, "BV1race"))
            self.assertIn("正在停止", mgr.last_retry_error)

            # 放行旧线程退出
            allow_worker_exit.set()
            deadline = time.time() + 3.0
            while task.is_worker_running() and time.time() < deadline:
                time.sleep(0.02)
            self.assertFalse(task.is_worker_running())
            self.assertTrue(task.to_dict()["can_retry"])

            # 旧线程退出后重试成功，且使用全新的 cancel_event
            self.assertTrue(mgr.retry_task(auth_mock, "BV1race"))
            deadline = time.time() + 3.0
            while task.status != "success" and time.time() < deadline:
                time.sleep(0.02)

            self.assertEqual(task.status, "success")
            self.assertEqual(max_concurrent_workers, 1)
            self.assertEqual(len(observed_events), 2)
            self.assertIsNot(observed_events[0], observed_events[1])
            self.assertTrue(observed_events[0].is_set())
            self.assertFalse(observed_events[1].is_set())

        mgr.executor.shutdown(wait=True)

    def test_2_deduplication_key_separates_audio_and_video(self):
        """同一视频的音频和视频请求使用包含类型与质量的去重键，不会被合并覆盖。"""
        mgr = download_manager.DownloadManager(max_workers=2)
        gate = threading.Event()

        def fake_download(dl_self, video_info, quality="1080P", audio_only=False, audio_quality="30280", block_paid=True, progress_callback=None, cancel_event=None):
            gate.wait(timeout=3.0)
            ext = "m4a" if audio_only else "mp4"
            out_file = self.root / f"{video_info['bvid']}.{ext}"
            out_file.write_bytes(f"content-{ext}".encode("utf-8"))
            return str(out_file)

        auth_mock = MagicMock()
        auth_mock.is_login = True
        auth_mock.mid = ""

        with patch.object(downloader.MediaDownloader, "download", new=fake_download):
            vinfo = {"bvid": "BV1both", "视频名字": "音视频双下", "UP名字": "测试UP"}
            t_audio = mgr.add_task(auth_mock, vinfo, quality="1080P", audio_only=True, audio_quality="30280", download_dir=str(self.root))
            t_video = mgr.add_task(auth_mock, vinfo, quality="1080P", audio_only=False, audio_quality="30280", download_dir=str(self.root))
            # 相同参数的重复请求才复用同一个任务
            t_audio_dup = mgr.add_task(auth_mock, vinfo, quality="1080P", audio_only=True, audio_quality="30280", download_dir=str(self.root))

            self.assertIs(t_audio, t_audio_dup)
            self.assertIsNot(t_audio, t_video)
            self.assertNotEqual(t_audio.task_key, t_video.task_key)

            gate.set()
            deadline = time.time() + 3.0
            while (t_audio.status != "success" or t_video.status != "success") and time.time() < deadline:
                time.sleep(0.02)

            self.assertEqual(t_audio.status, "success")
            self.assertEqual(t_video.status, "success")
            self.assertTrue((self.root / "BV1both.m4a").exists())
            self.assertTrue((self.root / "BV1both.mp4").exists())

        mgr.executor.shutdown(wait=True)

    def test_3_retry_uses_fixed_task_account_id(self):
        """任务保存稳定的账号 ID，切换全局账号后重试仍按原账号 ID 取凭据；账号不存在时报错提示。"""
        db.save_account(mid="1001", uname="账号A", face="", cookie="SESSDATA=cookie_A; bili_jct=csrf_A; DedeUserID=1001", is_current=1)
        db.save_account(mid="2002", uname="账号B", face="", cookie="SESSDATA=cookie_B; bili_jct=csrf_B; DedeUserID=2002", is_current=0)

        mgr = download_manager.DownloadManager(max_workers=1)
        used_cookies = []
        should_fail = True

        def fake_download(dl_self, video_info, quality="1080P", audio_only=False, audio_quality="30280", block_paid=True, progress_callback=None, cancel_event=None):
            cookie_val = getattr(dl_self.auth, "cookie", {})
            sessdata = cookie_val.get("SESSDATA", "") if isinstance(cookie_val, dict) else str(cookie_val)
            used_cookies.append(sessdata)
            if should_fail:
                raise RuntimeError("模拟网络超时")
            out_file = self.root / "BV1acc.m4a"
            out_file.write_bytes(b"ok")
            return str(out_file)

        auth_a = MagicMock()
        auth_a.is_login = True
        auth_a.mid = "1001"
        auth_a.uname = "账号A"
        auth_a.cookie_str = "SESSDATA=cookie_A; bili_jct=csrf_A; DedeUserID=1001"

        auth_b = MagicMock()
        auth_b.is_login = True
        auth_b.mid = "2002"
        auth_b.uname = "账号B"
        auth_b.cookie_str = "SESSDATA=cookie_B; bili_jct=csrf_B; DedeUserID=2002"

        orig_init = download_manager.DownloadTask.__init__
        def fast_init(task_self, *args, **kwargs):
            orig_init(task_self, *args, **kwargs)
            task_self.max_retries = 1

        with patch.object(downloader.MediaDownloader, "download", new=fake_download), \
             patch.object(download_manager.DownloadTask, "__init__", new=fast_init):
            vinfo = {"bvid": "BV1acc", "视频名字": "账号绑定测试", "UP名字": "UP", "account_id": "1001", "account_name": "账号A"}
            task = mgr.add_task(auth_a, vinfo, quality="1080P", audio_only=True, download_dir=str(self.root))

            deadline = time.time() + 4.0
            while task.status != "failed" and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(task.status, "failed")
            self.assertEqual(task.account_id, "1001")

            # 切换全局活动账号到 B，并在全局传入 auth_b 的情况下重试任务 A
            db.set_current_account("2002")
            should_fail = False
            self.assertTrue(mgr.retry_task(auth_b, task.task_key))

            deadline = time.time() + 4.0
            while task.status != "success" and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(task.status, "success")
            self.assertIn("cookie_A", used_cookies[-1])
            self.assertNotIn("cookie_B", used_cookies[-1])
            self.assertEqual(task.account_name, "账号A")

            # 若删除原账号 1001，再次失败后重试应提示重新选择账号
            db.remove_account("1001")
            task.status = "failed"
            self.assertFalse(mgr.retry_task(auth_b, task.task_key))
            self.assertIn("重新选择账号", mgr.last_retry_error)

        mgr.executor.shutdown(wait=True)

    def test_4_existing_complete_file_protected_on_failed_redownload(self):
        """重新下载失败或取消时，临时文件被清理，已有的完整文件与数据库完成状态不丢失。"""
        up_dir = self.root / "bilibili" / "测试UP"
        up_dir.mkdir(parents=True, exist_ok=True)
        existing_m4a = up_dir / "已有视频_BV1keep.m4a"
        original_bytes = b"complete-original-audio-content"
        existing_m4a.write_bytes(original_bytes)

        db.upsert_videos([{
            "bvid": "BV1keep",
            "title": "已有视频",
            "author": "测试UP",
            "up_mid": 123,
        }])
        db.update_download_record("BV1keep", "success", str(existing_m4a))

        auth_mock = MagicMock()
        auth_mock.is_login = True
        dl = downloader.MediaDownloader(auth=auth_mock, download_dir=str(self.root))

        fake_playurl = {
            "code": 0,
            "data": {
                "dash": {
                    "audio": [{"id": 30280, "baseUrl": "https://example.com/audio.m4s", "bandwidth": 128000}],
                    "video": [],
                }
            }
        }

        with patch.object(dl, "inspect_video_meta", return_value=(111, 120)), \
             patch.object(downloader.BiliApi, "get_play_url", return_value=fake_playurl), \
             patch.object(dl, "download_stream", side_effect=RuntimeError("下载中途断网")):
            with self.assertRaises(RuntimeError):
                dl.download(
                    {"bvid": "BV1keep", "视频名字": "已有视频", "UP名字": "测试UP"},
                    audio_only=True,
                )

        # 原文件完好无损，且没有残留 .part 临时文件
        self.assertTrue(existing_m4a.exists())
        self.assertEqual(existing_m4a.read_bytes(), original_bytes)
        part_files = list(up_dir.glob("*.part"))
        self.assertEqual(part_files, [])

        # 数据库更新为 failed 时，因为原文件仍完整存在，保持 success 状态不丢记录
        db.update_download_record("BV1keep", "failed")
        status_map = db.get_all_download_status()
        self.assertEqual(status_map["BV1keep"]["status"], "success")
        self.assertEqual(status_map["BV1keep"]["file_path"], str(existing_m4a))

    def test_5_ffmpeg_output_redirected_to_log_file_and_atomic_mux_replace(self):
        """FFmpeg 混流输出重定向到日志文件而非 PIPE，且混流成功后才原子替换最终 mp4。"""
        up_dir = self.root / "bilibili" / "测试UP"
        up_dir.mkdir(parents=True, exist_ok=True)
        existing_mp4 = up_dir / "混流测试_BV1mux.mp4"
        existing_mp4.write_bytes(b"old-complete-mp4")

        auth_mock = MagicMock()
        auth_mock.is_login = True
        dl = downloader.MediaDownloader(auth=auth_mock, download_dir=str(self.root))

        fake_playurl = {
            "code": 0,
            "data": {
                "dash": {
                    "video": [{"id": 80, "baseUrl": "https://example.com/v.m4s", "bandwidth": 1000, "codecs": "avc1"}],
                    "audio": [{"id": 30280, "baseUrl": "https://example.com/a.m4s", "bandwidth": 500}],
                }
            }
        }

        def fake_stream(stream_url, output_path, bvid, progress_callback=None, cancel_event=None):
            Path(output_path).write_bytes(b"stream-bytes")

        captured_popen_kwargs = {}

        class DummyProc:
            returncode = 0
            def poll(self):
                return 0

        def fake_popen(cmd, **kwargs):
            captured_popen_kwargs.update(kwargs)
            stdout_fh = kwargs.get("stdout")
            if stdout_fh and hasattr(stdout_fh, "write"):
                stdout_fh.write("frame=1000 fps=60\n" * 500)
                stdout_fh.flush()
            mux_out = Path(cmd[-1])
            mux_out.write_bytes(b"new-muxed-mp4-data")
            return DummyProc()

        with patch.object(dl, "inspect_video_meta", return_value=(111, 120)), \
             patch.object(downloader.BiliApi, "get_play_url", return_value=fake_playurl), \
             patch.object(dl, "download_stream", side_effect=fake_stream), \
             patch("subprocess.Popen", side_effect=fake_popen):
            res_path = dl.download(
                {"bvid": "BV1mux", "视频名字": "混流测试", "UP名字": "测试UP"},
                audio_only=False,
            )

        self.assertEqual(res_path, str(existing_mp4))
        self.assertEqual(existing_mp4.read_bytes(), b"new-muxed-mp4-data")
        import subprocess
        self.assertNotEqual(captured_popen_kwargs.get("stdout"), subprocess.PIPE)
        self.assertEqual(captured_popen_kwargs.get("stderr"), subprocess.STDOUT)

    def test_6_startup_converts_leftover_downloading_to_interrupted(self):
        """启动初始化时将遗留的 downloading/pending 转换为 interrupted，并展示为“已中断，可重新下载”。"""
        db.upsert_videos([{
            "bvid": "BV1leftover",
            "title": "未完成视频",
            "author": "UP主",
            "up_mid": 1,
        }])
        db.update_download_record("BV1leftover", "downloading")

        # 模拟重启再次调用 init_db
        db.init_db()
        status_map = db.get_all_download_status()
        self.assertEqual(status_map["BV1leftover"]["status"], "interrupted")

        mgr = download_manager.DownloadManager(max_workers=1)
        tasks = mgr.get_all_tasks()
        matched = [t for t in tasks if t["bvid"] == "BV1leftover"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["status"], "failed")
        self.assertEqual(matched[0]["speed"], "已中断，可重新下载")
        self.assertEqual(matched[0]["error"], "已中断，可重新下载")
        mgr.executor.shutdown(wait=True)

    def test_7_updater_verifies_length_rejects_empty_and_surfaces_launch_error(self):
        """更新流程：拒绝空文件、核对 Content-Length 长度、启动安装器失败时返回实际错误。"""
        empty_exe = self.root / "empty_setup.exe"
        empty_exe.write_bytes(b"")
        ok, msg = server._launch_update_installer(str(empty_exe), reveal_in_folder=False)
        self.assertFalse(ok)
        self.assertIn("为空", msg)

        # 模拟 Content-Length 为 100 字节但实际只返回 10 字节
        class FakeUrlopenResp(io.BytesIO):
            def __init__(self, data: bytes, headers: dict):
                super().__init__(data)
                self.headers = headers
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()

        def fake_urlopen(req, timeout=25):
            return FakeUrlopenResp(b"0123456789", {"Content-Length": "100"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch.object(db, "get_download_dir", return_value=str(self.root)):
            server._update_download_state["status"] = "idle"
            server._cached_update_info = None
            req = server.UpdateDownloadRequest(
                download_url="https://example.com/setup.exe",
                file_name="test_len_setup.exe",
                auto_launch=False,
            )
            server.start_update_download(req)
            deadline = time.time() + 3.0
            while server._update_download_state["status"] == "downloading" and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(server._update_download_state["status"], "failed")
            self.assertIn("文件长度校验不一致", server._update_download_state["error"])



    def test_8_platform_isolated_sqlite_db_and_cookie_migration(self):
        """SQLite ???? Cookie ????????????????????????????"""
        import sqlite3
        from utils.crypto import encrypt_secret

        mig_dir = self.root / "legacy_migration_case"
        mig_dir.mkdir(parents=True, exist_ok=True)

        # ??????? bili_data.db ? zhihu_accounts/<hash>.db
        legacy_bili = mig_dir / "bili_data.db"
        conn = sqlite3.connect(str(legacy_bili))
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE accounts (mid TEXT PRIMARY KEY, uname TEXT, face TEXT, cookie TEXT, refresh_token TEXT, is_current INTEGER, is_enabled INTEGER, auto_refresh INTEGER, vip_type INTEGER, created_at INTEGER, updated_at INTEGER)")
        conn.execute("CREATE TABLE zhihu_accounts (url_token TEXT PRIMARY KEY, uname TEXT, face TEXT, headline TEXT, cookie TEXT, is_current INTEGER, is_enabled INTEGER, auto_refresh INTEGER, created_at INTEGER, updated_at INTEGER)")
        conn.execute("CREATE TABLE encrypted_sessions (session_key TEXT PRIMARY KEY, encrypted_payload TEXT, updated_at INTEGER)")
        conn.execute("INSERT INTO accounts VALUES ('100', 'BiliUser', '', ?, '', 1, 1, 1, 0, 1, 1)", (encrypt_secret("SESSDATA=bili_secret"),))
        conn.execute("INSERT INTO zhihu_accounts VALUES ('zh_user', 'ZhihuUser', '', '', ?, 1, 1, 1, 1, 1)", (encrypt_secret("z_c0=zhihu_secret"),))
        conn.execute("INSERT INTO encrypted_sessions VALUES ('bili_active_session', ?, 1)", (encrypt_secret('{"cookie": "SESSDATA=bili_secret"}'),))
        conn.execute("INSERT INTO encrypted_sessions VALUES ('zhihu_active_session', ?, 1)", (encrypt_secret('{"cookies": {"z_c0": "zhihu_secret"}}'),))
        conn.commit()
        conn.close()

        legacy_zh_dir = mig_dir / "zhihu_accounts"
        legacy_zh_dir.mkdir(parents=True, exist_ok=True)
        legacy_acc_db = legacy_zh_dir / "deadbeef.db"
        zconn = sqlite3.connect(str(legacy_acc_db))
        zconn.execute("CREATE TABLE zhihu_creators (url_token TEXT PRIMARY KEY, name TEXT)")
        zconn.execute("INSERT INTO zhihu_creators VALUES ('creator_1', '????')")
        zconn.commit()
        zconn.close()

        # ????????
        db.set_data_dir(str(mig_dir))
        db.init_db()

        # 1. ???????? db ????????? db/bilibili ? db/zhihu
        self.assertFalse(legacy_bili.exists())
        self.assertFalse(legacy_acc_db.exists())
        self.assertTrue((mig_dir / "db" / "bilibili" / "bilibili.db").exists())
        self.assertTrue((mig_dir / "db" / "zhihu" / "zhihu_auth.db").exists())
        self.assertTrue((mig_dir / "db" / "zhihu" / "accounts" / "deadbeef.db").exists())

        # 2. Cookie ????????????????
        self.assertEqual(db.get_current_account()["cookie"], "SESSDATA=bili_secret")
        self.assertEqual(db.get_current_zhihu_account()["cookie"], "z_c0=zhihu_secret")
        self.assertEqual(db.load_encrypted_session_blob("bili_active_session")["cookie"], "SESSDATA=bili_secret")
        self.assertEqual(db.load_encrypted_session_blob("zhihu_active_session")["cookies"]["z_c0"], "zhihu_secret")

        # ?? bilibili.db ????? zhihu_active_session ? zhihu_accounts ?
        bconn = db.get_db_connection()
        bcur = bconn.cursor()
        bcur.execute("SELECT 1 FROM encrypted_sessions WHERE session_key = 'zhihu_active_session'")
        self.assertIsNone(bcur.fetchone())
        bcur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='zhihu_accounts'")
        self.assertIsNone(bcur.fetchone())
        bconn.close()



    def test_9_auth_token_middleware_enforcement(self):
        """?? AUTH_TOKEN ???????? Token ???? 401?? CORS ??????? X-Auth-Token ???"""
        import asyncio
        import json

        async def _call(path: str, headers=None):
            messages = []
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                messages.append(message)
            raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
            await server.app({
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "path": path,
                "raw_path": path.encode(),
                "root_path": "",
                "scheme": "http",
                "query_string": b"",
                "server": ("127.0.0.1", 18888),
                "client": ("127.0.0.1", 50000),
                "headers": raw_headers,
            }, receive, send)
            start_msg = messages[0]
            resp_headers = {k.decode().lower(): v.decode() for k, v in start_msg.get("headers", [])}
            body = b"".join(m.get("body", b"") for m in messages[1:])
            return start_msg["status"], resp_headers, json.loads(body.decode("utf-8"))

        with patch.object(server, "AUTH_TOKEN", "secret-token-12345"):
            # 1. ? Token ??????? 401 ?????? CORS ?
            st1, h1, b1 = asyncio.run(_call("/api/settings"))
            self.assertEqual(st1, 401)
            self.assertEqual(h1.get("access-control-allow-origin"), "*")
            self.assertIn("Unauthorized", b1.get("detail", ""))

            # 2. ?? Token ? 401 ??
            st2, _, _ = asyncio.run(_call("/api/settings", {"X-Auth-Token": "wrong-token"}))
            self.assertEqual(st2, 401)

            # 3. ?? X-Auth-Token ???? 200
            st3, _, b3 = asyncio.run(_call("/api/settings", {"X-Auth-Token": "secret-token-12345"}))
            self.assertEqual(st3, 200)
            self.assertIn("download_dir", b3)

            # 4. /api/health ? Token ?????? 200
            st4, _, b4 = asyncio.run(_call("/api/health"))
            self.assertEqual(st4, 200)
            self.assertEqual(b4.get("status"), "ok")


if __name__ == "__main__":
    unittest.main()
