import os
import sys

# Ensure local imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import db
from zhihu_router import zhihu_client
import zhihu_router

def test_zhihu_yanxuan_guard():
    db.init_db()
    print("[TEST] 1. Testing Zhihu Yanxuan URL rejection...")
    test_urls = [
        "https://www.zhihu.com/market/paid_column/123456/section/789",
        "https://www.zhihu.com/remix/albums/123456",
        "https://www.zhihu.com/xen/market/mall/123",
        "https://km.zhihu.com/course/123456"
    ]
    for url in test_urls:
        try:
            zhihu_client.fetch_single_content_by_url(url)
            assert False, f"URL {url} should have been rejected as Yanxuan!"
        except ValueError as e:
            assert "盐选" in str(e), f"Expected Yanxuan message, got: {e}"
            print(f"  [PASS] Successfully rejected: {url}")

    print("[TEST] 2. Testing Zhihu content normalization paid detection...")
    dummy_item_paid = {
        "id": "test_paid_1",
        "type": "answer",
        "url": "https://www.zhihu.com/question/1/answer/2",
        "title": "测试付费回答",
        "content": "<p>前言...</p><p>最低 0.3 元/天开通盐选专栏查看完整内容</p>",
        "paid_info": {"is_paid": True},
        "author": {"name": "某盐选作者", "url_token": "yanxuan_author"}
    }
    normalized = zhihu_client._normalize_content_item(dummy_item_paid, "creator", "yanxuan_author")
    assert normalized["is_paid"] == 1, f"Expected is_paid == 1, got {normalized['is_paid']}"
    assert normalized["is_restricted"] == 1, f"Expected is_restricted == 1, got {normalized['is_restricted']}"
    print(f"  [PASS] Correctly detected paid item: is_paid={normalized['is_paid']}, reason={normalized['paid_reason']}")

    print("[TEST] 3. Testing Zhihu archive submission rejection for paid...")
    # Upsert into db
    db.upsert_zhihu_contents([normalized])
    try:
        zhihu_router._submit_archive_task(normalized)
        assert False, "Should have rejected archive task for paid item!"
    except ValueError as e:
        assert "盐选" in str(e) or "付费" in str(e)
        print(f"  [PASS] Correctly intercepted archive submission: {e}")

def test_bili_collections_db():
    db.init_db()
    print("[TEST] 4. Testing Bili collections and dual-switch DB methods...")
    mid = 99999999
    # Ensure clean initial state
    conn = db.get_db_connection()
    conn.execute("DELETE FROM authors WHERE mid = ?", (mid,))
    conn.execute("DELETE FROM bili_collections WHERE mid = ?", (mid,))
    conn.commit()
    conn.close()

    try:
        # Add dummy author
        db.save_authors([{"mid": mid, "uname": "测试UP主", "face": "test_face"}])
        assert db.get_author_monitor_status(mid) == 1

        # Toggle UP monitor
        ok = db.toggle_author_monitor(mid, False)
        assert ok is True
        assert db.get_author_monitor_status(mid) == 0
        print("  [PASS] Toggle UP monitor status to 0 verified.")

        # Save collections
        sample_cols = [
            {"mid": mid, "collection_type": "season", "collection_id": "1001", "title": "硬核合集A", "cover": "c1.jpg", "total": 10},
            {"mid": mid, "collection_type": "series", "collection_id": "2001", "title": "系列教程B", "cover": "c2.jpg", "total": 5}
        ]
        db.save_bili_collections(mid, sample_cols)
        cols = db.get_bili_collections(mid)
        assert len(cols) == 2
        assert cols[0]["title"] in ["硬核合集A", "系列教程B"]
        assert cols[0]["is_monitored"] == 0  # 默认合集不监听
        print(f"  [PASS] Saved and retrieved {len(cols)} collections (default monitor: 0).")

        # Toggle collection monitor to 1 (开启监听特定合集)
        ok2 = db.toggle_bili_collection_monitor(mid, "season", 1001, True)
        assert ok2 is True
        cols_after = db.get_bili_collections(mid)
        col_season = next(c for c in cols_after if c["collection_id"] == 1001)
        assert col_season["is_monitored"] == 1
        print("  [PASS] Toggled collection 1001 monitor to 1 (ON) verified.")
    finally:
        # Clean up test data
        conn = db.get_db_connection()
        conn.execute("DELETE FROM authors WHERE mid = ?", (mid,))
        conn.execute("DELETE FROM bili_collections WHERE mid = ?", (mid,))
        conn.commit()
        conn.close()
        zconn = db.get_zhihu_db_connection()
        zconn.execute("DELETE FROM zhihu_contents WHERE content_id = ?", ("test_paid_1",))
        zconn.commit()
        zconn.close()
        print("  [PASS] Test clean up completed.")

if __name__ == "__main__":
    test_zhihu_yanxuan_guard()
    test_bili_collections_db()
    print("\n[SUCCESS] ALL CUSTOM VERIFICATIONS PASSED!")
