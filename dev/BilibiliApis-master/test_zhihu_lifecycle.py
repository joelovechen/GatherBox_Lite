"""Offline lifecycle regression tests. All storage lives in temporary directories."""
import os
import asyncio
import json
import logging
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

_storage = tempfile.TemporaryDirectory(prefix="gatherbox-lifecycle-")
os.environ["BILI_DATA_DIR"] = _storage.name
from utils import db
db.init_db()
with patch("utils.paths.get_app_root", return_value=Path(_storage.name)):
    import zhihu_core as core
    import zhihu_router as router
from fastapi import HTTPException
core.app_logger.disabled = True


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="gatherbox-case-")
        db.set_data_dir(self.directory.name)
        db.init_db()
        self.auth = core.ZhihuAuthManager()
        self.auth_patch = patch.object(router, "zhihu_auth", self.auth)
        self.auth_patch.start()
        self.network = patch.object(core.cffi_requests, "Session", side_effect=AssertionError("Unexpected network"))
        self.network.start()

    def tearDown(self):
        self.auth_patch.stop()
        self.network.stop()
        self.directory.cleanup()

    def response(self, token="A", status=200):
        return Mock(status_code=status, cookies={}, text="{}", json=lambda: {
            "id": token, "url_token": token, "name": token,
        })

    def login(self, token="A"):
        with patch.object(core.ZhihuAuthManager, "_new_session", return_value=Mock(get=Mock(return_value=self.response(token)))):
            result = self.auth.import_cookie_string(f"z_c0=fake-{token}; d_c0=device-{token}")
        self.assertTrue(result[0])

    def test_failed_import_preserves_active_account(self):
        self.login()
        before = self.auth.get_cookie_header()
        with patch.object(core.ZhihuAuthManager, "_new_session", return_value=Mock(get=Mock(side_effect=TimeoutError()))):
            self.assertFalse(self.auth.import_cookie_string("z_c0=fake-B; d_c0=B")[0])
        self.assertEqual(self.auth.get_cookie_header(), before)
        self.assertEqual(self.auth.user_profile["url_token"], "A")
        self.assertEqual(db.get_current_zhihu_account()["url_token"], "A")

    def test_missing_token_and_wrong_identity_rejected(self):
        self.login()
        self.assertFalse(self.auth.import_cookie_string("d_c0=device")[0])
        with patch.object(core.ZhihuAuthManager, "_new_session", return_value=Mock(get=Mock(return_value=self.response("B")))):
            self.assertFalse(self.auth.import_cookie_string("z_c0=fake-B; d_c0=B", "A")[0])
        self.assertEqual(self.auth.user_profile["url_token"], "A")

    def test_switch_failure_is_not_committed(self):
        self.login()
        db.save_zhihu_account("B", "B", "", "z_c0=expired; d_c0=B", is_current=0)
        with patch.object(core.ZhihuAuthManager, "_new_session", return_value=Mock(get=Mock(return_value=self.response(status=401)))):
            with self.assertRaises(HTTPException):
                router.zhihu_switch_account({"url_token": "B"})
        self.assertEqual(db.get_current_zhihu_account()["url_token"], "A")
        self.assertTrue(self.auth.is_login)

    def test_logout_survives_restart(self):
        self.login()
        router.zhihu_logout()
        restarted = core.ZhihuAuthManager()
        self.assertFalse(restarted.is_login)
        self.assertIsNone(db.get_current_zhihu_account())
        self.assertFalse(router.zhihu_auth_status()["is_login"])
        self.assertEqual(len(db.get_zhihu_accounts()), 1)

    def test_disable_then_remove_does_not_resurrect(self):
        self.login()
        router.zhihu_update_account_flags({"url_token": "A", "is_enabled": False})
        self.assertFalse(self.auth.is_login)
        self.assertFalse(router.run_zhihu_auto_check_once(scheduled=True)["success"])
        router.zhihu_remove_account({"url_token": "A"})
        self.assertEqual(router.zhihu_list_accounts()["accounts"], [])
        self.assertFalse(router.zhihu_auth_status()["is_login"])

    def test_401_clears_identity_and_saved_credential(self):
        self.login()
        with patch.object(self.auth, "_new_session", return_value=Mock(get=Mock(return_value=self.response(status=401)))):
            self.assertFalse(self.auth.verify_login()[0])
        self.assertIsNone(self.auth.user_profile)
        self.assertFalse(self.auth.is_login)
        self.assertEqual(db.get_zhihu_account_by_token("A")["cookie"], "")

    def test_cancel_during_verification_cannot_commit(self):
        self.login()
        def response(*args, **kwargs):
            self.auth.cancel_login()
            return self.response("B")
        with patch.object(core.ZhihuAuthManager, "_new_session", return_value=Mock(get=response)):
            self.assertFalse(self.auth.import_cookie_string("z_c0=fake-B; d_c0=B")[0])
        self.assertEqual(self.auth.user_profile["url_token"], "A")

    def test_browser_cancel_and_failed_browser_do_not_use_old_session(self):
        self.login()
        from utils import zhihu_browser_login
        def immediate(target, **kwargs):
            return Mock(start=target)
        with patch.object(core.threading, "Thread", side_effect=immediate), patch.object(zhihu_browser_login, "run_browser_login", return_value=1):
            self.auth.cancel_login()
            self.auth.start_browser_login()
            self.assertEqual(self.auth.get_browser_login_status()["status"], "failed")
        def cancelled(**kwargs):
            self.auth.cancel_login()
            kwargs["on_cookies"]({"z_c0": "fake-B", "d_c0": "B"})
            return 0
        with patch.object(core.threading, "Thread", side_effect=immediate), patch.object(zhihu_browser_login, "run_browser_login", side_effect=cancelled):
            self.auth.start_browser_login()
        self.assertEqual(self.auth.user_profile["url_token"], "A")
        self.assertEqual(self.auth.get_browser_login_status()["status"], "cancelled")

    def test_account_data_cursor_and_legacy_isolation(self):
        with db.zhihu_workspace(""):
            db.save_zhihu_creator({"url_token": "legacy", "name": "Legacy"})
        for token in ("A", "B"):
            with db.zhihu_workspace(token):
                db.save_zhihu_creator({"url_token": "same", "name": token})
                db.save_zhihu_collections([{"collection_id": "1", "title": token}])
                db.upsert_zhihu_contents([{"content_id": "1", "title": token, "source_type": "single"}])
                db.set_zhihu_setting("zhihu_latest_upvote_time", token)
        for token in ("A", "B"):
            with db.zhihu_workspace(token):
                self.assertEqual(db.get_zhihu_creators()[0]["name"], token)
                self.assertEqual(db.get_zhihu_collections()[0]["title"], token)
                self.assertEqual(db.query_zhihu_contents()[0]["title"], token)
                self.assertEqual(db.get_zhihu_setting("zhihu_latest_upvote_time"), token)
        with db.zhihu_workspace(""):
            self.assertEqual(db.get_zhihu_creators()[0]["name"], "Legacy")

    def test_persistence_failure_does_not_switch_memory(self):
        self.login()
        with patch.object(core.ZhihuAuthManager, "_new_session", return_value=Mock(get=Mock(return_value=self.response("B")))), patch.object(db, "activate_zhihu_session", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.auth.import_cookie_string("z_c0=fake-B; d_c0=B")
        self.assertEqual(self.auth.user_profile["url_token"], "A")
        self.assertEqual(db.get_current_zhihu_account()["url_token"], "A")

    def test_queued_archive_keeps_account_and_stops_after_disable(self):
        self.login()
        item = {"content_id": "1", "title": "A", "source_type": "single", "paid_check_version": 2}
        with db.zhihu_workspace("A"):
            db.upsert_zhihu_contents([item])
            with patch.object(router._archive_executor, "submit") as submit:
                task = router._submit_archive_task(item)
                worker = submit.call_args.args[0]
        self.login("B")
        router.zhihu_update_account_flags({"url_token": "A", "is_enabled": False})
        with patch.object(router.ZhihuArchiver, "archive_item") as archive:
            worker()
            archive.assert_not_called()
        self.assertEqual(task["account_id"], "A")
        self.assertEqual(task["status"], "failed")
        with db.zhihu_workspace("A"):
            self.assertEqual(db.get_zhihu_content_detail("1")["download_status"], "failed")
        with db.zhihu_workspace("B"):
            self.assertIsNone(db.get_zhihu_content_detail("1"))

    def test_qrcode_requires_verified_identity_and_rejects_cancelled_key(self):
        self.login()
        self.auth.qr_token = "qr-test"
        self.auth._qr_cookies = {"d_c0": "qr-device"}
        response = Mock(status_code=200, cookies={}, text="{}", content=b"{}",
                        json=lambda: {"user_id": "B"})
        with patch.object(self.auth, "_get_auth_session", return_value=Mock(get=Mock(return_value=response))):
            self.assertEqual(self.auth.poll_qrcode("qr-test")["status"], "error")
        self.assertEqual(self.auth.user_profile["url_token"], "A")
        self.auth.cancel_login()
        self.assertEqual(self.auth.poll_qrcode("qr-test")["status"], "error")

    def test_saved_session_network_failure_does_not_log_out(self):
        self.login()
        restarted = core.ZhihuAuthManager()
        def immediate(target, **kwargs):
            return Mock(start=target)
        with patch.object(router, "zhihu_auth", restarted), patch.object(router.threading, "Thread", side_effect=immediate), patch.object(core.ZhihuAuthManager, "_new_session", return_value=Mock(get=Mock(side_effect=TimeoutError()))):
            result = router.zhihu_auth_status()
        self.assertTrue(result["is_login"])
        self.assertEqual(result["url_token"], "A")
        self.assertEqual(result["verification_state"], "unavailable")
        self.assertTrue(db.get_zhihu_account_by_token("A")["cookie"])

    def test_verification_recovers_after_timeout_and_ignores_logout_race(self):
        self.login()
        self.auth._verification_next = 0
        immediate = lambda target, **kw: Mock(start=target)
        with patch.object(router.threading, "Thread", side_effect=immediate), patch.object(core.ZhihuAuthManager, "_new_session", return_value=Mock(get=Mock(side_effect=TimeoutError()))):
            self.assertTrue(router.zhihu_auth_status()["is_login"])
        self.auth._verification_next = 0
        with patch.object(router.threading, "Thread", side_effect=immediate), patch.object(core.ZhihuAuthManager, "_new_session", return_value=Mock(get=Mock(return_value=self.response()))):
            self.assertEqual(router.zhihu_auth_status()["verification_state"], "verified")
        self.auth._verification_next = 0
        def logout_during_request(*args, **kwargs):
            self.auth.logout()
            return self.response()
        with patch.object(router.threading, "Thread", side_effect=immediate), patch.object(core.ZhihuAuthManager, "_new_session", return_value=Mock(get=logout_during_request)):
            self.assertFalse(router.zhihu_auth_status()["is_login"])
        self.assertFalse(core.ZhihuAuthManager().is_login)

    def test_confirmed_paid_legacy_content_stays_blocked(self):
        old = {"content_id": "1", "title": "Paid", "content_type": "answer", "is_paid": 1}
        db.upsert_zhihu_contents([old])
        fresh = core.zhihu_client._normalize_content_item({"id": "1", "paid_info": {"is_paid": True}})
        with patch.object(router.zhihu_client, "get_full_answer", return_value=fresh), patch.object(router._archive_executor, "submit") as submit:
            with self.assertRaises(HTTPException) as error:
                router.archive_zhihu_items({"content_id": "1"})
            self.assertEqual(error.exception.status_code, 403)
            submit.assert_not_called()
        self.assertEqual(db.get_zhihu_content_detail("1")["paid_check_version"], 2)

    def test_restored_session_401_marks_expired(self):
        self.login()
        restarted = core.ZhihuAuthManager()
        with patch.object(router, "zhihu_auth", restarted), patch.object(router.threading, "Thread", side_effect=lambda target, **kw: Mock(start=target)), patch.object(core.ZhihuAuthManager, "_new_session", return_value=Mock(get=Mock(return_value=self.response(status=401)))):
            result = router.zhihu_auth_status()
        self.assertFalse(result["is_login"])
        self.assertEqual(result["verification_state"], "expired")
        self.assertEqual(db.get_zhihu_account_by_token("A")["cookie"], "")

    def test_public_metadata_is_not_a_paywall(self):
        cases = [{"relationship": {"is_authorized": False}}, {"content_needs_truncated": True},
                 {"label_info": {"text": "普通专栏"}}, {"paid_info": {"type": "free"}},
                 {"content": "这篇文章讨论知乎盐选的商业模式。"}]
        for fields in cases:
            with self.subTest(fields=fields):
                item = core.zhihu_client._normalize_content_item({"id": "1", **fields})
                self.assertFalse(item["is_paid"])
                self.assertEqual(item["paid_check_version"], 2)
        for fields in [{"paid_info": {"is_paid": True}}, {"label_info": {"text": "盐选专栏"}}]:
            self.assertTrue(core.zhihu_client._normalize_content_item({"id": "1", **fields})["is_paid"])
        self.assertEqual(core.zhihu_client._normalize_content_item({"id": "1", "paid_info": {"type": "unknown"}})["paid_check_version"], 0)

    def test_legacy_paywall_is_rechecked_without_destroying_read_state(self):
        old = {"content_id": "1", "title": "Example", "content_type": "answer", "source_type": "creator", "source_id": "author", "is_paid": 1, "paid_reason": "未授权付费内容", "is_read": 0}
        db.upsert_zhihu_contents([old])
        shown = router.get_zhihu_contents_list()["items"][0]
        self.assertFalse(shown["is_paid"])
        self.assertTrue(shown["access_pending"])
        with patch.object(router.zhihu_client, "get_full_answer", return_value=None):
            with self.assertRaises(HTTPException):
                router._refresh_content_access(db.get_zhihu_content_detail("1"))
        self.assertEqual(db.get_zhihu_content_detail("1")["is_paid"], 1)
        fresh = core.zhihu_client._normalize_content_item({"id": "1", "content": "<p>Public answer</p>"})
        with patch.object(router.zhihu_client, "get_full_answer", return_value=fresh):
            router._refresh_content_access(db.get_zhihu_content_detail("1"))
        saved = db.get_zhihu_content_detail("1")
        self.assertFalse(saved["is_paid"])
        self.assertEqual(saved["paid_check_version"], 2)
        self.assertEqual(saved["is_read"], 0)
        self.assertEqual(saved["source_id"], "author")

    def test_http_route_signatures_and_logout(self):
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router.zhihu_router)
        self.login()
        async def request(method, path, body=None):
            messages = []
            async def receive():
                return {"type": "http.request", "body": json.dumps(body).encode() if body else b"", "more_body": False}
            async def send(message):
                messages.append(message)
            await app({"type": "http", "http_version": "1.1", "method": method,
                       "path": path, "raw_path": path.encode(), "root_path": "",
                       "scheme": "http", "query_string": b"", "server": ("test", 80),
                       "client": ("test", 1), "headers": [(b"content-type", b"application/json")]}, receive, send)
            self.assertEqual(messages[0]["status"], 200)
            return json.loads(b"".join(m.get("body", b"") for m in messages))
        asyncio.run(request("POST", "/api/zhihu/auth/update_account_flags", {"url_token": "A", "auto_refresh": False}))
        asyncio.run(request("GET", "/api/zhihu/contents"))
        asyncio.run(request("POST", "/api/zhihu/auth/logout"))
        self.assertFalse(asyncio.run(request("GET", "/api/zhihu/auth/status"))["is_login"])

    def test_snapshot_credentials_and_persistence_are_independent(self):
        self.login()
        snapshot = self.auth.snapshot()
        self.login("B")
        self.assertEqual(snapshot.user_profile["url_token"], "A")
        snapshot.invalidate()
        self.assertEqual(self.auth.user_profile["url_token"], "B")
        self.assertTrue(db.get_zhihu_account_by_token("A")["cookie"])

    def test_cross_account_retry_uses_task_owner_credentials(self):
        router._archive_tasks.clear()
        self.login("A")
        with db.zhihu_workspace("A"):
            db.upsert_zhihu_contents([{
                "content_id": "101",
                "title": "A's Article",
                "content_type": "article",
                "content_html": "<p>Valid body content with more than forty characters for A.</p>",
                "paid_check_version": 2,
                "is_paid": 0,
            }])
        # Switch active session to B
        self.login("B")
        self.assertEqual(self.auth.user_profile["url_token"], "B")

        # Seed a failed task owned by A
        router._archive_tasks["A:101"] = {
            "content_id": "101",
            "bvid": "zhihu_A_101",
            "platform": "zhihu",
            "account_name": "A",
            "account_id": "A",
            "status": "failed",
            "updated_at": 1,
        }

        captured_clients = []
        orig_archiver = router.ZhihuArchiver
        def fake_archiver(client):
            captured_clients.append(client)
            return orig_archiver(client)

        with patch.object(router, "ZhihuArchiver", side_effect=fake_archiver), patch.object(router._archive_executor, "submit"):
            retried = router.retry_zhihu_archive_tasks(bvids=["zhihu_A_101"])
        self.assertEqual(retried, 1)
        self.assertEqual(len(captured_clients), 1)
        self.assertEqual(captured_clients[0].auth.user_profile["url_token"], "A")
        self.assertIn("z_c0=fake-A", captured_clients[0].auth.get_cookie_header())
        self.assertEqual(router._archive_tasks["A:101"]["account_name"], "A")
        self.assertEqual(router._archive_tasks["A:101"]["account_id"], "A")

        # If A is disabled, retrying A's task must fail instead of falling back to B
        router._archive_tasks["A:101"]["status"] = "failed"
        router.zhihu_update_account_flags({"url_token": "A", "is_enabled": False})
        with patch.object(router._archive_executor, "submit") as sub_mock:
            retried_disabled = router.retry_zhihu_archive_tasks(bvids=["zhihu_A_101"])
            self.assertEqual(retried_disabled, 0)
            sub_mock.assert_not_called()


    def test_legacy_owner_workspace_migration_and_disclaimer_persistence(self):
        # 1. Seed legacy data in main DB (pre-workspace tables) belonging to owner 'owner-tok'
        with db.zhihu_workspace(""):
            db.save_zhihu_creator({"url_token": "c1", "name": "Creator 1"})
            db.save_zhihu_collections([{
                "collection_id": "col1",
                "title": "My Fav",
                "creator_token": "owner-tok",
            }])
            db.upsert_zhihu_contents([{
                "content_id": "ans1",
                "title": "Legacy Answer",
                "source_type": "creator",
                "source_id": "c1",
            }])
        db.set_setting("zhihu_upvote_token", "owner-tok")
        self.login("owner-tok")

        # Owner workspace automatically inherits the legacy data from main DB
        self.assertEqual(len(db.get_zhihu_creators()), 1)
        self.assertEqual(db.get_zhihu_creators()[0]["url_token"], "c1")
        self.assertEqual(len(db.get_zhihu_collections()), 1)
        self.assertEqual(len(db.query_zhihu_contents()), 1)
        self.assertEqual(db.get_zhihu_setting("zhihu_upvote_token"), "owner-tok")

        # A second account 'other-tok' must NOT inherit owner-tok's legacy data
        self.login("other-tok")
        self.assertEqual(len(db.get_zhihu_creators()), 0)
        self.assertEqual(len(db.get_zhihu_collections()), 0)
        self.assertEqual(len(db.query_zhihu_contents()), 0)

        # 2. Disclaimer auto-heals to 'true' on init_db when accounts already exist
        db.set_setting("disclaimer_accepted", "false")
        db.init_db()
        self.assertEqual(db.get_setting("disclaimer_accepted"), "true")


def tearDownModule():
    for logger_obj in (core.app_logger, logging.getLogger()):
        for h in list(logger_obj.handlers):
            try:
                h.flush()
                h.close()
            except Exception:
                pass
            logger_obj.removeHandler(h)
    logging.shutdown()
    try:
        _storage.cleanup()
    except Exception:
        pass


if __name__ == "__main__":
    unittest.main()
