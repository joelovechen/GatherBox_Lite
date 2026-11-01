"""SQLite 本地数据持久化与增量同步引擎.

作用：
1. 本地优先读取：应用启动或切换分组时直接读 SQLite，响应时间 < 10ms，无需等待网络请求。
2. 增量同步策略：根据博主已知的最新视频时间戳 (latest_created)，抓取第 1 页时一旦遇到已有视频立即截断停止，
   避免重复爬取已入库的历史稿件。
3. 全局搜索支持：基于 SQLite LIKE / FTS 支持跨博主、跨全库毫秒级标题与 UP 名字搜索。
4. 下载状态持久化：记录每个视频的本地保存文件路径和下载状态，重启后依然保留。
5. 平台级存储隔离：
   - B站数据库与 Cookie 凭据：{data_dir}/db/bilibili/bilibili.db
   - 知乎鉴权与 Cookie 凭据：{data_dir}/db/zhihu/zhihu_auth.db
   - 知乎多账号独立工作区：{data_dir}/db/zhihu/accounts/<sha256(url_token)>.db
"""

import json
import hashlib
import shutil
import threading
from contextvars import ContextVar
from contextlib import contextmanager
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from utils.crypto import decrypt_secret, encrypt_secret, is_encrypted
from utils.paths import default_data_dir

DATA_DIR = Path(default_data_dir())
DB_FILE = DATA_DIR / "db" / "bilibili" / "bilibili.db"
ZHIHU_AUTH_DB_FILE = DATA_DIR / "db" / "zhihu" / "zhihu_auth.db"
ZHIHU_ACCOUNTS_DB_DIR = DATA_DIR / "db" / "zhihu" / "accounts"

_RESERVED_ROOT_DIRS = {"bilibili", "zhihu", "db", "_updates", "zhihu_accounts"}


def _resolve_data_root(db_path: Optional[str] = None) -> Path:
    target = Path(db_path) if db_path else DB_FILE
    if target.parent.name.lower() in ("bilibili", "zhihu") and target.parent.parent.name.lower() == "db":
        return target.parent.parent.parent
    return target.parent


def _get_zhihu_auth_db_path() -> Path:
    return _resolve_data_root() / "db" / "zhihu" / "zhihu_auth.db"


def _get_zhihu_accounts_db_dir() -> Path:
    return _resolve_data_root() / "db" / "zhihu" / "accounts"


def _backup_sqlite_file(src_path: Path, dst_path: Path) -> bool:
    """使用 SQLite 在线备份 API 将源库（含 WAL 未刷盘数据）完整合并备份到目标路径."""
    try:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        src_conn = sqlite3.connect(str(src_path), timeout=15)
        dst_conn = sqlite3.connect(str(dst_path), timeout=15)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        return True
    except Exception:
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_path), str(dst_path))
            return True
        except Exception:
            return False


def _cleanup_sqlite_files(db_file: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_file) + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


def set_data_dir(data_dir: str):
    global DATA_DIR, DB_FILE, ZHIHU_AUTH_DB_FILE, ZHIHU_ACCOUNTS_DB_DIR
    p = Path(data_dir)
    p.mkdir(parents=True, exist_ok=True)
    DATA_DIR = p
    DB_FILE = p / "db" / "bilibili" / "bilibili.db"
    ZHIHU_AUTH_DB_FILE = p / "db" / "zhihu" / "zhihu_auth.db"
    ZHIHU_ACCOUNTS_DB_DIR = p / "db" / "zhihu" / "accounts"
    _zhihu_initialized.clear()
    _migrate_storage_layout_if_needed(p)


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    target_path = Path(db_path) if db_path else DB_FILE
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=15000;")
    except Exception:
        pass
    return conn


def _ensure_zhihu_auth_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS zhihu_accounts (
        url_token TEXT PRIMARY KEY,
        uname TEXT NOT NULL,
        face TEXT DEFAULT '',
        headline TEXT DEFAULT '',
        cookie TEXT NOT NULL,
        is_current INTEGER DEFAULT 0,
        is_enabled INTEGER DEFAULT 1,
        auto_refresh INTEGER DEFAULT 1,
        created_at INTEGER,
        updated_at INTEGER
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS encrypted_sessions (
        session_key TEXT PRIMARY KEY,
        encrypted_payload TEXT NOT NULL,
        updated_at INTEGER DEFAULT 0
    )
    """)
    conn.commit()


def get_zhihu_auth_db_connection() -> sqlite3.Connection:
    """获取知乎平台独立鉴权与 Cookie 数据库连接 ({data_dir}/db/zhihu/zhihu_auth.db)."""
    auth_path = _get_zhihu_auth_db_path()
    conn = get_db_connection(str(auth_path))
    _ensure_zhihu_auth_schema(conn)
    return conn


def _migrate_storage_layout_if_needed(data_root: Path) -> None:
    """
    自动将旧版混放的 SQLite 数据库与下载目录平滑迁移至按平台隔离的新结构：
    - {data_root}/bili_data.db -> {data_root}/db/bilibili/bilibili.db (仅保留 B站数据与 B站 Cookie)
    - 知乎账号与 Cookie -> {data_root}/db/zhihu/zhihu_auth.db
    - {data_root}/zhihu_accounts/<sha256>.db -> {data_root}/db/zhihu/accounts/<sha256>.db
    - 根目录下的散落 B站 UP主下载目录 -> {download_dir}/bilibili/{UP主}/
    """
    if not data_root.exists():
        return

    bili_db = data_root / "db" / "bilibili" / "bilibili.db"
    zhihu_auth_db = data_root / "db" / "zhihu" / "zhihu_auth.db"
    zhihu_acc_dir = data_root / "db" / "zhihu" / "accounts"

    # 1. 迁移旧版主库 bili_data.db -> db/bilibili/bilibili.db
    legacy_bili_db = data_root / "bili_data.db"
    if legacy_bili_db.exists():
        if not bili_db.exists() or bili_db.stat().st_size == 0:
            if _backup_sqlite_file(legacy_bili_db, bili_db):
                _cleanup_sqlite_files(legacy_bili_db)
        else:
            _cleanup_sqlite_files(legacy_bili_db)

    # 2. 迁移旧版知乎账号库目录 zhihu_accounts/*.db -> db/zhihu/accounts/*.db
    legacy_zhihu_dir = data_root / "zhihu_accounts"
    if legacy_zhihu_dir.exists() and legacy_zhihu_dir.is_dir():
        zhihu_acc_dir.mkdir(parents=True, exist_ok=True)
        for item in list(legacy_zhihu_dir.iterdir()):
            if item.is_file() and item.suffix.lower() == ".db":
                dst_db = zhihu_acc_dir / item.name
                if not dst_db.exists() or dst_db.stat().st_size == 0:
                    _backup_sqlite_file(item, dst_db)
                _cleanup_sqlite_files(item)
            elif item.is_file() and (item.name.endswith("-wal") or item.name.endswith("-shm")):
                try:
                    item.unlink()
                except OSError:
                    pass
            elif item.is_dir():
                # 若旧版在 zhihu_accounts/<hash>/zhihu/ 下归档了文章，迁回 {data_root}/zhihu/
                nested_zhihu = item / "zhihu"
                if nested_zhihu.exists() and nested_zhihu.is_dir():
                    target_zhihu_root = data_root / "zhihu"
                    target_zhihu_root.mkdir(parents=True, exist_ok=True)
                    for cat_dir in list(nested_zhihu.iterdir()):
                        dst_cat = target_zhihu_root / cat_dir.name
                        if not dst_cat.exists():
                            try:
                                shutil.move(str(cat_dir), str(dst_cat))
                            except Exception:
                                pass
                    try:
                        nested_zhihu.rmdir()
                        item.rmdir()
                    except OSError:
                        pass
        try:
            legacy_zhihu_dir.rmdir()
        except OSError:
            pass

    # 3. 将 bilibili.db 中混存的知乎账号、知乎 Cookie (zhihu_active_session)、知乎配置与无作用域表剥离至 db/zhihu/zhihu_auth.db
    if bili_db.exists():
        bconn = get_db_connection(str(bili_db))
        zconn = get_db_connection(str(zhihu_auth_db))
        try:
            _ensure_zhihu_auth_schema(zconn)
            bcur = bconn.cursor()
            zcur = zconn.cursor()

            bcur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            b_tables = {r["name"] for r in bcur.fetchall()}

            if "zhihu_accounts" in b_tables:
                bcur.execute("SELECT * FROM zhihu_accounts")
                for r in bcur.fetchall():
                    d = dict(r)
                    zcur.execute("""
                    INSERT OR IGNORE INTO zhihu_accounts
                        (url_token, uname, face, headline, cookie, is_current, is_enabled, auto_refresh, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        d.get("url_token", ""),
                        d.get("uname", ""),
                        d.get("face", ""),
                        d.get("headline", ""),
                        d.get("cookie", ""),
                        int(d.get("is_current") or 0),
                        int(d.get("is_enabled") if d.get("is_enabled") is not None else 1),
                        int(d.get("auto_refresh") if d.get("auto_refresh") is not None else 1),
                        d.get("created_at"),
                        d.get("updated_at"),
                    ))
                bcur.execute("DROP TABLE IF EXISTS zhihu_accounts")

            if "encrypted_sessions" in b_tables:
                bcur.execute("SELECT session_key, encrypted_payload, updated_at FROM encrypted_sessions WHERE session_key LIKE 'zhihu%'")
                for r in bcur.fetchall():
                    zcur.execute("""
                    INSERT OR IGNORE INTO encrypted_sessions (session_key, encrypted_payload, updated_at)
                    VALUES (?, ?, ?)
                    """, (r["session_key"], r["encrypted_payload"], r["updated_at"]))
                bcur.execute("DELETE FROM encrypted_sessions WHERE session_key LIKE 'zhihu%'")

            if "settings" in b_tables:
                bcur.execute("SELECT key, value FROM settings WHERE key LIKE 'zhihu%'")
                for r in bcur.fetchall():
                    zcur.execute("""
                    INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)
                    """, (r["key"], r["value"]))
                bcur.execute("DELETE FROM settings WHERE key LIKE 'zhihu%'")

            # 若旧版 bilibili.db 中含有未迁移的知乎内容表，一并迁入 zhihu_auth.db 后从 bilibili.db 删除
            if {"zhihu_creators", "zhihu_collections", "zhihu_contents"} & b_tables:
                zconn.commit()
                init_zhihu_tables(str(zhihu_auth_db))
                if "zhihu_creators" in b_tables:
                    bcur.execute("SELECT * FROM zhihu_creators")
                    for c in bcur.fetchall():
                        cd = dict(c)
                        zcur.execute("""
                        INSERT OR IGNORE INTO zhihu_creators
                            (url_token, user_id, name, avatar_url, headline, answer_count, articles_count, follower_count, group_name, last_sync_time, latest_created)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            cd.get("url_token", ""), cd.get("user_id", ""), cd.get("name", ""),
                            cd.get("avatar_url", ""), cd.get("headline", ""),
                            int(cd.get("answer_count") or 0), int(cd.get("articles_count") or 0),
                            int(cd.get("follower_count") or 0), cd.get("group_name") or "默认分组",
                            int(cd.get("last_sync_time") or 0), int(cd.get("latest_created") or 0),
                        ))
                    bcur.execute("DROP TABLE IF EXISTS zhihu_creators")
                if "zhihu_collections" in b_tables:
                    bcur.execute("SELECT * FROM zhihu_collections")
                    for col in bcur.fetchall():
                        cold = dict(col)
                        zcur.execute("""
                        INSERT OR IGNORE INTO zhihu_collections
                            (collection_id, title, description, creator_name, creator_token, item_count, follower_count, source_type, last_sync_time, latest_created)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            cold.get("collection_id", ""), cold.get("title", ""), cold.get("description", ""),
                            cold.get("creator_name", ""), cold.get("creator_token", ""),
                            int(cold.get("item_count") or 0), int(cold.get("follower_count") or 0),
                            cold.get("source_type") or "my", int(cold.get("last_sync_time") or 0),
                            int(cold.get("latest_created") or 0),
                        ))
                    bcur.execute("DROP TABLE IF EXISTS zhihu_collections")
                if "zhihu_contents" in b_tables:
                    bcur.execute("SELECT * FROM zhihu_contents")
                    for item in bcur.fetchall():
                        it = dict(item)
                        zcur.execute("""
                        INSERT OR IGNORE INTO zhihu_contents (
                            content_id, source_type, source_id, content_type, question_id,
                            title, excerpt, content_html, content_url, cover_url,
                            author_name, author_token, author_avatar,
                            voteup_count, comment_count, created_time, updated_time, upvote_time,
                            is_read, download_status, local_file, is_paid, paid_reason, paid_check_version, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            it.get("content_id", ""), it.get("source_type", "creator"), it.get("source_id", ""),
                            it.get("content_type", "answer"), it.get("question_id", ""),
                            it.get("title", ""), it.get("excerpt", ""), it.get("content_html", ""),
                            it.get("content_url", ""), it.get("cover_url", ""),
                            it.get("author_name", ""), it.get("author_token", ""), it.get("author_avatar", ""),
                            int(it.get("voteup_count") or 0), int(it.get("comment_count") or 0),
                            int(it.get("created_time") or 0), int(it.get("updated_time") or 0),
                            int(it.get("upvote_time") or 0), int(it.get("is_read") if it.get("is_read") is not None else 1),
                            it.get("download_status") or "none", it.get("local_file") or "",
                            int(it.get("is_paid") or 0), it.get("paid_reason") or "",
                            int(it.get("paid_check_version") or 0), int(it.get("updated_at") or 0),
                        ))
                    bcur.execute("DROP TABLE IF EXISTS zhihu_contents")

            zconn.commit()
            bconn.commit()
        except Exception:
            pass
        finally:
            zconn.close()
            bconn.close()

    # 4. 将下载根目录下旧版未按平台一级分类的 B站 UP主文件夹迁入 {download_dir}/bilibili/{UP主}
    try:
        dl_root = data_root
        if bili_db.exists():
            conn = get_db_connection(str(bili_db))
            try:
                cur = conn.cursor()
                cur.execute("SELECT value FROM settings WHERE key = 'download_dir'")
                row = cur.fetchone()
                if row and row["value"]:
                    candidate_dl = Path(row["value"])
                    # 修复曾因 DB_FILE.parent 被误设为 db/bilibili 的情况
                    if candidate_dl.name.lower() == "bilibili" and candidate_dl.parent.name.lower() == "db":
                        candidate_dl = candidate_dl.parent.parent
                        cur.execute("UPDATE settings SET value = ? WHERE key = 'download_dir'", (str(candidate_dl),))
                        conn.commit()
                    dl_root = candidate_dl
            except Exception:
                pass
            finally:
                conn.close()

        if dl_root.exists() and dl_root.is_dir():
            for sub in list(dl_root.iterdir()):
                if not sub.is_dir() or sub.name.startswith(".") or sub.name.lower() in _RESERVED_ROOT_DIRS:
                    continue
                has_bili_media = any(
                    f.is_file() and f.suffix.lower() in (".mp4", ".m4a", ".json")
                    for f in sub.iterdir()
                )
                if has_bili_media:
                    bili_platform_dir = dl_root / "bilibili"
                    bili_platform_dir.mkdir(parents=True, exist_ok=True)
                    target_up_dir = bili_platform_dir / sub.name
                    if not target_up_dir.exists():
                        shutil.move(str(sub), str(target_up_dir))
                    else:
                        for f in list(sub.iterdir()):
                            dst_f = target_up_dir / f.name
                            if not dst_f.exists():
                                shutil.move(str(f), str(dst_f))
                        try:
                            sub.rmdir()
                        except OSError:
                            pass
    except Exception:
        pass


def init_db(db_path: Optional[str] = None):
    """初始化 B 站与知乎数据库表结构与索引."""
    data_root = _resolve_data_root(db_path)
    if db_path is None:
        _migrate_storage_layout_if_needed(data_root)

    conn = get_db_connection(db_path)
    cur = conn.cursor()

    # 设置与配置表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    # 默认设置项（默认下载根目录为 data_root，而非 db/bilibili 子目录）
    cur.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('download_dir', ?)",
        (str(data_root),),
    )
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('download_max_workers', '3')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_check_enabled', 'false')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_check_interval', '30')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_check_scope', 'special')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_download_new', 'false')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('theme', 'dark')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('default_video_quality', '1080P')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('default_audio_quality', '30280')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('block_paid_videos', 'true')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('disclaimer_accepted', 'false')")

    # 多账号管理表 (B站)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS accounts (
        mid TEXT PRIMARY KEY,
        uname TEXT NOT NULL,
        face TEXT DEFAULT '',
        cookie TEXT NOT NULL,
        refresh_token TEXT DEFAULT '',
        is_current INTEGER DEFAULT 0,
        is_enabled INTEGER DEFAULT 1,
        auto_refresh INTEGER DEFAULT 1,
        vip_type INTEGER DEFAULT 0,
        created_at INTEGER,
        updated_at INTEGER
    )
    """)
    for col_sql in [
        "ALTER TABLE accounts ADD COLUMN is_enabled INTEGER DEFAULT 1",
        "ALTER TABLE accounts ADD COLUMN auto_refresh INTEGER DEFAULT 1",
    ]:
        try:
            cur.execute(col_sql)
        except Exception:
            pass

    # 博主表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS authors (
        mid INTEGER PRIMARY KEY,
        uname TEXT NOT NULL,
        face TEXT,
        sign TEXT,
        last_sync_time INTEGER DEFAULT 0,
        latest_created INTEGER DEFAULT 0,
        is_monitored INTEGER DEFAULT 1
    )
    """)
    try:
        cur.execute("ALTER TABLE authors ADD COLUMN is_monitored INTEGER DEFAULT 1")
    except Exception:
        pass

    # B站 UP 主合集与系列表 (支持两级细粒度监听)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bili_collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mid INTEGER NOT NULL,
        collection_type TEXT NOT NULL, -- 'season' or 'series'
        collection_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        cover TEXT DEFAULT '',
        total INTEGER DEFAULT 0,
        is_monitored INTEGER DEFAULT 0,
        last_sync_time INTEGER DEFAULT 0,
        updated_at INTEGER DEFAULT 0,
        UNIQUE(mid, collection_type, collection_id)
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bili_collections_mid ON bili_collections(mid)")

    # 博主与分组关联表 (一个博主可属于多个分组)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS author_tags (
        mid INTEGER NOT NULL,
        tagid INTEGER NOT NULL,
        PRIMARY KEY (mid, tagid)
    )
    """)

    # 视频库表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS videos (
        bvid TEXT PRIMARY KEY,
        aid INTEGER,
        title TEXT NOT NULL,
        author TEXT NOT NULL,
        up_mid INTEGER NOT NULL,
        created INTEGER NOT NULL,
        pubdate TEXT,
        url TEXT,
        play INTEGER DEFAULT 0,
        length TEXT,
        pic TEXT,
        download_status TEXT DEFAULT 'none',
        local_file TEXT DEFAULT '',
        is_read INTEGER DEFAULT 1,
        is_restricted INTEGER DEFAULT 0,
        restrict_reason TEXT DEFAULT '',
        updated_at INTEGER
    )
    """)
    for v_col_sql in [
        "ALTER TABLE videos ADD COLUMN is_restricted INTEGER DEFAULT 0",
        "ALTER TABLE videos ADD COLUMN restrict_reason TEXT DEFAULT ''",
    ]:
        try:
            cur.execute(v_col_sql)
        except Exception:
            pass

    # 一次性迁移：将旧库中默认 is_read=0 的存量历史视频重置为已读(1)，确保仅未来巡检新发现的稿件标为未读(0)
    cur.execute("SELECT value FROM settings WHERE key = 'unread_baseline_migrated'")
    if not cur.fetchone():
        cur.execute("UPDATE videos SET is_read = 1 WHERE is_read = 0")
        cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('unread_baseline_migrated', 'true')")

    # 索引以加速排序和搜索
    cur.execute("CREATE INDEX IF NOT EXISTS idx_videos_created ON videos(created DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_videos_up_mid ON videos(up_mid)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_videos_title ON videos(title)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_author_tags_tagid ON author_tags(tagid)")

    # 收藏夹表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS fav_folders (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        media_count INTEGER DEFAULT 0,
        cover TEXT,
        attr INTEGER DEFAULT 0,
        last_sync_time INTEGER DEFAULT 0,
        latest_fav_time INTEGER DEFAULT 0
    )
    """)

    # 收藏夹与视频关联表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS fav_videos (
        media_id INTEGER NOT NULL,
        bvid TEXT NOT NULL,
        fav_time INTEGER DEFAULT 0,
        PRIMARY KEY (media_id, bvid)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fav_videos_media ON fav_videos(media_id, fav_time DESC)")

    # 加密会话保险箱表（B站激活会话 Cookie）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS encrypted_sessions (
        session_key TEXT PRIMARY KEY,
        encrypted_payload TEXT NOT NULL,
        updated_at INTEGER DEFAULT 0
    )
    """)

    # 启动时轻量恢复：将上次退出遗留的 downloading/pending 假状态转为 interrupted（若文件已完整存在则标为 success）
    try:
        cur.execute("SELECT bvid, local_file FROM videos WHERE download_status IN ('downloading', 'pending')")
        for row in cur.fetchall():
            lf = row["local_file"] or ""
            if lf and os.path.exists(lf) and os.path.getsize(lf) > 0:
                cur.execute("UPDATE videos SET download_status = 'success' WHERE bvid = ?", (row["bvid"],))
            else:
                cur.execute("UPDATE videos SET download_status = 'interrupted' WHERE bvid = ?", (row["bvid"],))
    except Exception:
        pass

    conn.commit()
    conn.close()

    # 初始化知乎平台独立数据库 (db/zhihu/zhihu_auth.db) 与表结构
    zhihu_target = db_path if db_path else str(_get_zhihu_auth_db_path())
    zconn = get_db_connection(zhihu_target)
    try:
        _ensure_zhihu_auth_schema(zconn)
    finally:
        zconn.close()
    init_zhihu_tables(zhihu_target)

    # 迁移明文凭据（B站入 bilibili.db，知乎入 zhihu_auth.db）
    _migrate_plaintext_credentials(data_root, db_path=db_path)

    # 若数据库中已存在账号或加密会话记录，说明老用户此前已同意过免责声明
    try:
        bconn = get_db_connection(db_path)
        zconn2 = get_db_connection(zhihu_target)
        try:
            bcur = bconn.cursor()
            zcur = zconn2.cursor()
            bcur.execute("SELECT value FROM settings WHERE key = 'disclaimer_accepted'")
            disc_row = bcur.fetchone()
            if not disc_row or str(disc_row["value"]).lower() != "true":
                has_existing_state = False
                for tbl in ("accounts", "encrypted_sessions"):
                    bcur.execute(f"SELECT 1 FROM {tbl} LIMIT 1")
                    if bcur.fetchone():
                        has_existing_state = True
                        break
                if not has_existing_state:
                    for tbl in ("zhihu_accounts", "encrypted_sessions"):
                        zcur.execute(f"SELECT 1 FROM {tbl} LIMIT 1")
                        if zcur.fetchone():
                            has_existing_state = True
                            break
                if has_existing_state:
                    bcur.execute(
                        "INSERT INTO settings (key, value) VALUES ('disclaimer_accepted', 'true') "
                        "ON CONFLICT(key) DO UPDATE SET value = 'true'"
                    )
                    bconn.commit()
        finally:
            zconn2.close()
            bconn.close()
    except Exception:
        pass


def _migrate_plaintext_credentials(data_dir: Path, db_path: Optional[str] = None) -> None:
    """将数据库中旧版明文 Cookie/RefreshToken 原地加密，并将磁盘遗留明文会话文件按平台迁入对应数据库后安全删除."""
    now = int(time.time())
    bconn = get_db_connection(db_path)
    zconn = get_db_connection(db_path) if db_path else get_zhihu_auth_db_connection()

    try:
        bcur = bconn.cursor()
        zcur = zconn.cursor()

        # 1. 迁移 B站 accounts 表中的明文 cookie 与 refresh_token
        try:
            bcur.execute("SELECT mid, cookie, refresh_token FROM accounts")
            for row in bcur.fetchall():
                mid = row["mid"]
                raw_cookie = row["cookie"] or ""
                raw_rt = row["refresh_token"] or ""
                need_update = False
                enc_cookie = raw_cookie
                enc_rt = raw_rt
                if raw_cookie and not is_encrypted(raw_cookie):
                    enc_cookie = encrypt_secret(raw_cookie)
                    need_update = True
                if raw_rt and not is_encrypted(raw_rt):
                    enc_rt = encrypt_secret(raw_rt)
                    need_update = True
                if need_update:
                    bcur.execute(
                        "UPDATE accounts SET cookie = ?, refresh_token = ? WHERE mid = ?",
                        (enc_cookie, enc_rt, mid),
                    )
        except Exception:
            pass

        # 2. 迁移知乎 zhihu_accounts 表中的明文 cookie
        try:
            zcur.execute("SELECT url_token, cookie FROM zhihu_accounts")
            for row in zcur.fetchall():
                token = row["url_token"]
                raw_cookie = row["cookie"] or ""
                if raw_cookie and not is_encrypted(raw_cookie):
                    zcur.execute(
                        "UPDATE zhihu_accounts SET cookie = ? WHERE url_token = ?",
                        (encrypt_secret(raw_cookie), token),
                    )
        except Exception:
            pass

        # 3. 迁移并销毁磁盘上遗留的 B站 session.json / cookies.txt
        legacy_bili_session = data_dir / "session.json"
        legacy_bili_cookie = data_dir / "cookies.txt"
        bili_payload: Optional[dict] = None
        if legacy_bili_session.exists():
            try:
                data = json.loads(legacy_bili_session.read_text(encoding="utf-8").strip())
                if isinstance(data, dict) and data.get("cookie"):
                    bili_payload = {
                        "cookie": str(data.get("cookie", "")),
                        "refresh_token": str(data.get("refresh_token", "")),
                        "mid": str(data.get("mid", "")),
                        "updated_at": int(data.get("updated_at") or now),
                    }
            except Exception:
                pass
        if not bili_payload and legacy_bili_cookie.exists():
            try:
                raw_c = legacy_bili_cookie.read_text(encoding="utf-8").strip()
                if raw_c:
                    bili_payload = {"cookie": raw_c, "refresh_token": "", "mid": "", "updated_at": now}
            except Exception:
                pass
        if bili_payload:
            try:
                enc_str = encrypt_secret(json.dumps(bili_payload, ensure_ascii=False))
                bcur.execute(
                    "INSERT OR IGNORE INTO encrypted_sessions (session_key, encrypted_payload, updated_at) VALUES (?, ?, ?)",
                    ("bili_active_session", enc_str, now),
                )
            except Exception:
                pass
        for fpath in (legacy_bili_session, legacy_bili_cookie):
            if fpath.exists():
                try:
                    fpath.unlink()
                except OSError:
                    pass

        # 4. 迁移并销毁磁盘上遗留的知乎 zhihu_session.json -> 存入知乎独立库 zhihu_auth.db
        legacy_zhihu_session = data_dir / "zhihu_session.json"
        if legacy_zhihu_session.exists():
            try:
                zdata = json.loads(legacy_zhihu_session.read_text(encoding="utf-8").strip())
                if isinstance(zdata, dict):
                    enc_z = encrypt_secret(json.dumps(zdata, ensure_ascii=False))
                    zcur.execute(
                        "INSERT OR IGNORE INTO encrypted_sessions (session_key, encrypted_payload, updated_at) VALUES (?, ?, ?)",
                        ("zhihu_active_session", enc_z, now),
                    )
            except Exception:
                pass
            try:
                legacy_zhihu_session.unlink()
            except OSError:
                pass

        bconn.commit()
        zconn.commit()
    finally:
        zconn.close()
        bconn.close()


def _get_session_db_connection(session_key: str) -> sqlite3.Connection:
    """按平台路由 Cookie 加密会话存储库：zhihu* -> zhihu_auth.db，其余 -> bilibili.db."""
    if str(session_key or "").lower().startswith("zhihu"):
        return get_zhihu_auth_db_connection()
    return get_db_connection()


def save_encrypted_session_blob(session_key: str, payload: Dict[str, Any]) -> None:
    """将活跃会话字典加密后持久化至对应平台的 SQLite encrypted_sessions 表."""
    if not session_key or not isinstance(payload, dict):
        return
    raw_json = json.dumps(payload, ensure_ascii=False)
    enc_val = encrypt_secret(raw_json)
    now = int(time.time())
    conn = _get_session_db_connection(session_key)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS encrypted_sessions (
        session_key TEXT PRIMARY KEY,
        encrypted_payload TEXT NOT NULL,
        updated_at INTEGER DEFAULT 0
    )
    """)
    cur.execute("""
    INSERT INTO encrypted_sessions (session_key, encrypted_payload, updated_at)
    VALUES (?, ?, ?)
    ON CONFLICT(session_key) DO UPDATE SET
        encrypted_payload = excluded.encrypted_payload,
        updated_at = excluded.updated_at
    """, (session_key, enc_val, now))
    conn.commit()
    conn.close()


def load_encrypted_session_blob(session_key: str) -> Optional[Dict[str, Any]]:
    """从对应平台的 SQLite encrypted_sessions 表读取并解密活跃会话字典."""
    if not session_key:
        return None
    conn = _get_session_db_connection(session_key)
    cur = conn.cursor()
    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS encrypted_sessions (
            session_key TEXT PRIMARY KEY,
            encrypted_payload TEXT NOT NULL,
            updated_at INTEGER DEFAULT 0
        )
        """)
        cur.execute("SELECT encrypted_payload FROM encrypted_sessions WHERE session_key = ?", (session_key,))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row["encrypted_payload"]:
        return None
    plain_json = decrypt_secret(row["encrypted_payload"])
    if not plain_json:
        return None
    try:
        data = json.loads(plain_json)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def delete_encrypted_session_blob(session_key: str) -> None:
    """从对应平台的 SQLite encrypted_sessions 表删除指定的加密会话记录."""
    if not session_key:
        return
    conn = _get_session_db_connection(session_key)
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM encrypted_sessions WHERE session_key = ?", (session_key,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def save_authors(authors: List[dict], tagid: Optional[int] = None):
    """保存或更新博主信息，并维护分组关系."""
    if not authors:
        return
    conn = get_db_connection()
    cur = conn.cursor()

    for up in authors:
        mid = up.get("mid")
        if not mid:
            continue
        uname = up.get("uname", f"UP_{mid}").strip()
        face = up.get("face", "")
        sign = up.get("sign", "")

        cur.execute("""
        INSERT INTO authors (mid, uname, face, sign)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(mid) DO UPDATE SET
            uname = excluded.uname,
            face = CASE WHEN excluded.face != '' THEN excluded.face ELSE authors.face END,
            sign = excluded.sign
        """, (mid, uname, face, sign))

        if tagid is not None:
            cur.execute("""
            INSERT OR IGNORE INTO author_tags (mid, tagid) VALUES (?, ?)
            """, (mid, tagid))

    conn.commit()
    conn.close()


def get_authors_by_tag(tagid: int = -10, all_followings: bool = False) -> List[dict]:
    """从数据库读取指定分组或全量的博主列表 (包含监听状态 is_monitored)."""
    conn = get_db_connection()
    cur = conn.cursor()
    if all_followings:
        cur.execute("SELECT mid, uname, face, sign, last_sync_time, latest_created, COALESCE(is_monitored, 1) AS is_monitored FROM authors ORDER BY uname ASC")
    else:
        cur.execute("""
        SELECT a.mid, a.uname, a.face, a.sign, a.last_sync_time, a.latest_created, COALESCE(a.is_monitored, 1) AS is_monitored
        FROM authors a
        INNER JOIN author_tags at ON a.mid = at.mid
        WHERE at.tagid = ?
        ORDER BY a.uname ASC
        """, (tagid,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def toggle_author_monitor(mid: int, is_monitored: int) -> bool:
    """切换博主整体监听状态 (0=不监听, 1=监听)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE authors SET is_monitored = ? WHERE mid = ?", (1 if is_monitored else 0, mid))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_author_monitor_status(mid: int) -> int:
    """查询博主整体监听状态 (默认为 1 开启)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_monitored FROM authors WHERE mid = ?", (mid,))
    row = cur.fetchone()
    conn.close()
    if row and row["is_monitored"] is not None:
        return int(row["is_monitored"])
    return 1


def save_bili_collections(mid: int, collections: List[dict]):
    """保存或更新 UP 主的合集与系列列表，保留用户已设置的 is_monitored 监听开关."""
    if not collections:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    for c in collections:
        col_type = c.get("collection_type") or "season"
        col_id = int(c.get("collection_id") or 0)
        if not col_id:
            continue
        title = c.get("title") or c.get("name") or f"合集_{col_id}"
        cover = c.get("cover") or ""
        total = int(c.get("total") or 0)
        cur.execute("""
        INSERT INTO bili_collections (mid, collection_type, collection_id, title, cover, total, is_monitored, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mid, collection_type, collection_id) DO UPDATE SET
            title = excluded.title,
            cover = CASE WHEN excluded.cover != '' THEN excluded.cover ELSE bili_collections.cover END,
            total = excluded.total,
            updated_at = excluded.updated_at
        """, (mid, col_type, col_id, title, cover, total, int(c.get("is_monitored", 0)), now))
    conn.commit()
    conn.close()


def get_bili_collections(mid: int) -> List[dict]:
    """读取指定 UP 主已持久化的所有合集与系列列表."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
    SELECT id, mid, collection_type, collection_id, title, cover, total, is_monitored, last_sync_time, updated_at
    FROM bili_collections
    WHERE mid = ?
    ORDER BY is_monitored DESC, id ASC
    """, (mid,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def toggle_bili_collection_monitor(mid: int, collection_type: str, collection_id: int, is_monitored: int) -> bool:
    """切换指定合集/系列的独立监听开关 (0=关闭, 1=开启)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
    UPDATE bili_collections
    SET is_monitored = ?, updated_at = ?
    WHERE mid = ? AND collection_type = ? AND collection_id = ?
    """, (1 if is_monitored else 0, int(time.time()), mid, collection_type, collection_id))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_author_sync_meta(mid: int) -> Tuple[int, int]:
    """获取博主上次同步时间及已知最新视频发布时间戳 (latest_created, last_sync_time)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT latest_created, last_sync_time FROM authors WHERE mid = ?", (mid,))
    row = cur.fetchone()
    conn.close()
    if row:
        return row["latest_created"] or 0, row["last_sync_time"] or 0
    return 0, 0


def update_author_sync_meta(mid: int, latest_created: int):
    """更新博主增量标记."""
    conn = get_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    cur.execute("""
    UPDATE authors
    SET last_sync_time = ?,
        latest_created = MAX(latest_created, ?)
    WHERE mid = ?
    """, (now, latest_created, mid))
    conn.commit()
    conn.close()


def upsert_videos(video_list: List[dict], is_read_default: int = 1) -> int:
    """插入或更新视频，如果已下载则保留其下载状态与本地文件路径."""
    if not video_list:
        return 0
    conn = get_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    inserted_count = 0

    for v in video_list:
        bvid = v.get("bvid")
        if not bvid:
            continue
        aid = v.get("aid") or 0
        title = (v.get("视频名字") or v.get("title") or "").strip()
        author = (v.get("UP名字") or v.get("author") or "").strip()
        up_mid = v.get("UP_MID") or v.get("up_mid") or 0
        created = v.get("created") or 0
        pubdate = v.get("发布日期") or v.get("pubdate") or ""
        url = v.get("URL") or v.get("url") or f"https://www.bilibili.com/video/{bvid}"
        play = v.get("播放量") or v.get("play") or 0
        length = v.get("时长") or v.get("length") or ""
        pic = v.get("封面") or v.get("pic") or ""
        is_read_val = int(v.get("is_read")) if v.get("is_read") is not None else is_read_default
        is_restricted_val = int(bool(v.get("is_restricted") or v.get("restricted")))
        restrict_reason_val = str(v.get("restrict_reason") or "").strip()

        cur.execute("""
        INSERT INTO videos (
            bvid, aid, title, author, up_mid, created, pubdate, url, play, length, pic, is_read, is_restricted, restrict_reason, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bvid) DO UPDATE SET
            title = excluded.title,
            author = excluded.author,
            play = excluded.play,
            length = excluded.length,
            pic = CASE WHEN excluded.pic != '' THEN excluded.pic ELSE videos.pic END,
            is_restricted = CASE WHEN excluded.is_restricted > 0 THEN excluded.is_restricted ELSE COALESCE(videos.is_restricted, 0) END,
            restrict_reason = CASE WHEN excluded.restrict_reason != '' THEN excluded.restrict_reason ELSE COALESCE(videos.restrict_reason, '') END,
            updated_at = excluded.updated_at
        """, (bvid, aid, title, author, up_mid, created, pubdate, url, play, length, pic, is_read_val, is_restricted_val, restrict_reason_val, now))
        inserted_count += 1

    conn.commit()
    conn.close()
    return inserted_count


def mark_videos_read(bvid: Optional[str] = None) -> int:
    """将指定视频或全部未读追更视频标记为已读."""
    conn = get_db_connection()
    cur = conn.cursor()
    if bvid:
        cur.execute("UPDATE videos SET is_read = 1 WHERE bvid = ? AND is_read = 0", (bvid,))
    else:
        cur.execute("UPDATE videos SET is_read = 1 WHERE is_read = 0")
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected


def count_unread_videos() -> int:
    """统计当前未读的新追更视频数量."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM videos WHERE is_read = 0")
    row = cur.fetchone()
    conn.close()
    return row["cnt"] if row else 0


def query_videos(
    tagid: int = -10,
    all_followings: bool = True,
    search_kw: Optional[str] = None,
    up_mid: Optional[int] = None,
    limit: int = 500,
    offset: int = 0
) -> List[dict]:
    """查询视频列表（支持分组过滤、单UP主过滤与全库搜索）."""
    conn = get_db_connection()
    cur = conn.cursor()

    conditions = []
    params = []

    # 1. 关键词搜索 (跨标题、UP名字、BV号)
    if search_kw and search_kw.strip():
        kw_pattern = f"%{search_kw.strip()}%"
        conditions.append("(v.title LIKE ? OR v.author LIKE ? OR v.bvid LIKE ?)")
        params.extend([kw_pattern, kw_pattern, kw_pattern])

    # 2. 单博主筛选
    if up_mid:
        conditions.append("v.up_mid = ?")
        params.append(up_mid)
    elif not all_followings:
        # 分组筛选：只取属于该 tagid 的博主的视频
        conditions.append("v.up_mid IN (SELECT mid FROM author_tags WHERE tagid = ?)")
        params.append(tagid)

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"""
    SELECT
        v.bvid,
        v.aid,
        v.title,
        v.author,
        v.pubdate,
        v.url,
        v.up_mid,
        v.play,
        v.length AS duration,
        v.pic,
        v.title AS "视频名字",
        v.author AS "UP名字",
        v.pubdate AS "发布日期",
        v.created,
        v.url AS "URL",
        v.up_mid AS "UP_MID",
        v.play AS "播放量",
        v.length AS "时长",
        v.pic AS "封面",
        v.is_read,
        COALESCE(v.is_restricted, 0) AS is_restricted,
        COALESCE(v.restrict_reason, '') AS restrict_reason,
        v.download_status,
        v.local_file
    FROM videos v
    {where_clause}
    ORDER BY v.created DESC
    LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_videos(
    tagid: int = -10,
    all_followings: bool = True,
    search_kw: Optional[str] = None,
    up_mid: Optional[int] = None
) -> int:
    """统计满足条件的视频总数."""
    conn = get_db_connection()
    cur = conn.cursor()

    conditions = []
    params = []

    if search_kw and search_kw.strip():
        kw_pattern = f"%{search_kw.strip()}%"
        conditions.append("(v.title LIKE ? OR v.author LIKE ? OR v.bvid LIKE ?)")
        params.extend([kw_pattern, kw_pattern, kw_pattern])

    if up_mid:
        conditions.append("v.up_mid = ?")
        params.append(up_mid)
    elif not all_followings:
        conditions.append("v.up_mid IN (SELECT mid FROM author_tags WHERE tagid = ?)")
        params.append(tagid)

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"SELECT COUNT(*) FROM videos v {where_clause}"

    cur.execute(sql, params)
    total = cur.fetchone()[0]
    conn.close()
    return total


def update_download_record(bvid: str, status: str, local_file: Optional[str] = None):
    """更新视频的下载记录（保护磁盘上已有的完整文件不被失败或取消状态抹除）."""
    conn = get_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    if local_file:
        cur.execute("""
        UPDATE videos
        SET download_status = ?, local_file = ?, updated_at = ?
        WHERE bvid = ?
        """, (status, local_file, now, bvid))
    else:
        if status in ("none", "failed", "cancelled"):
            cur.execute("SELECT local_file FROM videos WHERE bvid = ?", (bvid,))
            row = cur.fetchone()
            existing_file = (row["local_file"] or "") if row else ""
            if existing_file and os.path.exists(existing_file) and os.path.getsize(existing_file) > 0:
                status = "success"
        cur.execute("""
        UPDATE videos
        SET download_status = ?, updated_at = ?
        WHERE bvid = ?
        """, (status, now, bvid))
    conn.commit()
    conn.close()


def mark_video_restricted(bvid: str, reason: str):
    """将视频标记为付费/充电专属/会员试看受限状态并持久化."""
    conn = get_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    cur.execute("""
    UPDATE videos
    SET is_restricted = 1,
        restrict_reason = ?,
        download_status = 'restricted',
        updated_at = ?
    WHERE bvid = ?
    """, (reason or "付费/受限", now, bvid))
    conn.commit()
    conn.close()


def get_all_download_status() -> Dict[str, dict]:
    """读取所有已标记的下载任务状态字典."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT bvid, title, author, pic, up_mid, download_status, local_file, updated_at FROM videos WHERE download_status != 'none'")
    rows = cur.fetchall()
    conn.close()
    res = {}
    for r in rows:
        res[r["bvid"]] = {
            "status": r["download_status"],
            "file_path": r["local_file"] or "",
            "title": r["title"] or r["bvid"],
            "author": r["author"] or "历史已下载",
            "pic": r["pic"] or "",
            "up_mid": r["up_mid"] or 0,
            "updated_at": r["updated_at"] or 0,
        }
    return res


def save_fav_folders(folders: List[dict]):
    """保存或更新用户收藏夹列表."""
    if not folders:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    now = int(time.time())

    for f in folders:
        fid = f.get("id")
        if not fid:
            continue
        cur.execute("""
        INSERT INTO fav_folders (id, title, media_count, cover, attr, last_sync_time)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            media_count = excluded.media_count,
            cover = excluded.cover,
            attr = excluded.attr
        """, (
            fid,
            f.get("title", ""),
            f.get("media_count", 0),
            f.get("cover", ""),
            f.get("attr", 0),
            now
        ))
    conn.commit()
    conn.close()


def get_fav_folders() -> List[dict]:
    """读取所有缓存的收藏夹列表."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title, media_count, cover, attr, last_sync_time, latest_fav_time FROM fav_folders ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_fav_folder_sync_meta(media_id: int) -> Tuple[int, int]:
    """获取收藏夹最近一次同步的最新收藏时间戳与同步时间."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT latest_fav_time, last_sync_time FROM fav_folders WHERE id = ?", (media_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return row["latest_fav_time"], row["last_sync_time"]
    return 0, 0


def update_fav_folder_sync_meta(media_id: int, latest_fav_time: int):
    """更新收藏夹的同步元数据."""
    conn = get_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    cur.execute("""
    UPDATE fav_folders
    SET latest_fav_time = MAX(latest_fav_time, ?), last_sync_time = ?
    WHERE id = ?
    """, (latest_fav_time, now, media_id))
    conn.commit()
    conn.close()


def save_fav_videos(media_id: int, videos: List[dict]):
    """批量保存收藏夹内视频并维护关联映射."""
    if not videos:
        return
    # 1. 保存到公共视频表 (保留已有下载状态)
    upsert_videos(videos)

    # 2. 插入到 fav_videos 关联表
    conn = get_db_connection()
    cur = conn.cursor()
    for v in videos:
        bvid = v.get("bvid")
        fav_time = v.get("fav_time", v.get("created", 0))
        if not bvid:
            continue
        cur.execute("""
        INSERT INTO fav_videos (media_id, bvid, fav_time)
        VALUES (?, ?, ?)
        ON CONFLICT(media_id, bvid) DO UPDATE SET
            fav_time = excluded.fav_time
        """, (media_id, bvid, fav_time))
    conn.commit()
    conn.close()


def count_fav_videos(media_id: int, search_kw: Optional[str] = None) -> int:
    """统计收藏夹内满足条件的视频总数."""
    conn = get_db_connection()
    cur = conn.cursor()
    conditions = ["fv.media_id = ?"]
    params = [media_id]

    if search_kw and search_kw.strip():
        kw_pattern = f"%{search_kw.strip()}%"
        conditions.append("(v.title LIKE ? OR v.author LIKE ? OR v.bvid LIKE ?)")
        params.extend([kw_pattern, kw_pattern, kw_pattern])

    where_clause = " WHERE " + " AND ".join(conditions)
    sql = f"SELECT COUNT(*) FROM fav_videos fv JOIN videos v ON fv.bvid = v.bvid {where_clause}"
    cur.execute(sql, params)
    total = cur.fetchone()[0]
    conn.close()
    return total


def query_fav_videos(
    media_id: int,
    search_kw: Optional[str] = None,
    limit: int = 500,
    offset: int = 0
) -> List[dict]:
    """分页读取指定收藏夹内的视频列表 (按收藏时间倒序)."""
    conn = get_db_connection()
    cur = conn.cursor()
    conditions = ["fv.media_id = ?"]
    params = [media_id]

    if search_kw and search_kw.strip():
        kw_pattern = f"%{search_kw.strip()}%"
        conditions.append("(v.title LIKE ? OR v.author LIKE ? OR v.bvid LIKE ?)")
        params.extend([kw_pattern, kw_pattern, kw_pattern])

    where_clause = " WHERE " + " AND ".join(conditions)
    sql = f"""
    SELECT
        v.bvid,
        v.aid,
        v.title,
        v.author,
        v.pubdate,
        v.url,
        v.up_mid,
        v.play,
        v.length AS duration,
        v.pic,
        v.title AS "视频名字",
        v.author AS "UP名字",
        v.pubdate AS "发布日期",
        v.created,
        v.url AS "URL",
        v.up_mid AS "UP_MID",
        v.play AS "播放量",
        v.length AS "时长",
        v.pic AS "封面",
        COALESCE(v.is_restricted, 0) AS is_restricted,
        COALESCE(v.restrict_reason, '') AS restrict_reason,
        v.download_status,
        v.local_file,
        fv.fav_time
    FROM fav_videos fv
    JOIN videos v ON fv.bvid = v.bvid
    {where_clause}
    ORDER BY fv.fav_time DESC, v.created DESC
    LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_setting_db_connection(key: str) -> sqlite3.Connection:
    if str(key or "").lower().startswith("zhihu"):
        return get_zhihu_auth_db_connection()
    return get_db_connection()


def get_setting(key: str, default: str = "") -> str:
    """获取系统持久化配置项（知乎相关配置自动路由至 zhihu_auth.db）."""
    conn = _get_setting_db_connection(key)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    """更新或插入配置项（知乎相关配置自动路由至 zhihu_auth.db）."""
    conn = _get_setting_db_connection(key)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO settings (key, value) VALUES (?, ?)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, value))
    conn.commit()
    conn.close()


def get_download_dir() -> str:
    """获取当前配置的下载保存根目录."""
    return get_setting("download_dir", str(_resolve_data_root()))


def reconcile_downloaded_files(download_dir: Optional[str] = None) -> Tuple[int, int]:
    """
    定期与按需对账磁盘上的文件与数据库记录：
    1. 检查所有 DB 中标记为 'success' 的记录：若其 local_file 在磁盘上不存在，先尝试在 {download_dir}/bilibili/ 下自愈定位，仍不存在则重置为 'none'。
    2. 扫描 {download_dir}/bilibili/（及兼容扫描根目录旧文件夹）下各博主子目录，自动认领已存在的 *_{bvid}.m4a 或 *_{bvid}.mp4。
    返回: (reset_count, restored_count)
    """
    dir_path = Path(download_dir or get_download_dir())
    if not dir_path.exists():
        return 0, 0

    bili_root = dir_path if dir_path.name.lower() == "bilibili" else (dir_path / "bilibili")

    conn = get_db_connection()
    cur = conn.cursor()

    reset_count = 0
    restored_count = 0
    now = int(time.time())

    # 1. 检查已下载的视频文件是否已被删除或迁移到 bilibili/ 子目录
    cur.execute("SELECT bvid, author, local_file FROM videos WHERE download_status = 'success'")
    success_rows = cur.fetchall()
    for r in success_rows:
        bvid = r["bvid"]
        local_file = r["local_file"] or ""
        if local_file and os.path.exists(local_file):
            continue
        # 尝试在 bilibili/{UP主}/ 下自愈定位同名文件
        healed_path = ""
        if local_file:
            old_p = Path(local_file)
            candidate = bili_root / old_p.parent.name / old_p.name
            if candidate.exists():
                healed_path = str(candidate.resolve())
        if healed_path:
            cur.execute("UPDATE videos SET local_file = ?, updated_at = ? WHERE bvid = ?", (healed_path, now, bvid))
        else:
            cur.execute("""
            UPDATE videos
            SET download_status = 'none', local_file = '', updated_at = ?
            WHERE bvid = ?
            """, (now, bvid))
            reset_count += 1

    # 2. 扫描下载目录下现有文件，识别并认领已存在的音视频
    cur.execute("SELECT bvid, author FROM videos WHERE download_status != 'success'")
    pending_rows = cur.fetchall()
    if pending_rows:
        pending_bvids = {r["bvid"] for r in pending_rows}
        scan_roots = []
        if bili_root.exists() and bili_root.is_dir():
            scan_roots.append(bili_root)
        if dir_path != bili_root:
            scan_roots.append(dir_path)
        try:
            for s_root in scan_roots:
                for up_folder in s_root.iterdir():
                    if not up_folder.is_dir() or up_folder.name.startswith("."):
                        continue
                    if s_root == dir_path and up_folder.name.lower() in _RESERVED_ROOT_DIRS:
                        continue
                    for file in up_folder.iterdir():
                        if (
                            file.is_file()
                            and not file.name.startswith((".temp_", "temp_"))
                            and not file.name.endswith(".part")
                            and file.suffix.lower() in [".m4a", ".mp4"]
                        ):
                            for bvid in list(pending_bvids):
                                if bvid in file.name:
                                    cur.execute("""
                                    UPDATE videos
                                    SET download_status = 'success', local_file = ?, updated_at = ?
                                    WHERE bvid = ?
                                    """, (str(file.resolve()), now, bvid))
                                    pending_bvids.discard(bvid)
                                    restored_count += 1
                                    break
        except Exception as e:
            print(f"[!] 磁盘扫描对账异常: {e}", flush=True)

    conn.commit()
    conn.close()
    if reset_count > 0 or restored_count > 0:
        print(f"[*] 磁盘文件对账自愈完成: 已清理失效记录 {reset_count} 条，自动认领现有文件 {restored_count} 条", flush=True)
    return reset_count, restored_count


# ---------------------------------------------------------------------- 多账号支持 (B站 -> db/bilibili/bilibili.db)
def save_account(mid: str, uname: str, face: str, cookie: str, refresh_token: str = "", vip_type: int = 0, is_current: int = 1):
    """保存或更新账号信息，若 is_current=1 则先将其他账号取消激活."""
    conn = get_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    if is_current:
        cur.execute("UPDATE accounts SET is_current = 0")

    cur.execute("""
    INSERT INTO accounts (mid, uname, face, cookie, refresh_token, is_current, vip_type, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(mid) DO UPDATE SET
        uname = excluded.uname,
        face = CASE WHEN excluded.face != '' THEN excluded.face ELSE accounts.face END,
        cookie = excluded.cookie,
        refresh_token = CASE WHEN excluded.refresh_token != '' THEN excluded.refresh_token ELSE accounts.refresh_token END,
        is_current = excluded.is_current,
        vip_type = excluded.vip_type,
        updated_at = excluded.updated_at
    """, (str(mid), uname, face, encrypt_secret(cookie), encrypt_secret(refresh_token) if refresh_token else "", is_current, vip_type, now, now))
    conn.commit()
    conn.close()


def get_accounts() -> List[dict]:
    """获取所有已保存账号列表（不泄露完整敏感 Cookie）."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT mid, uname, face, is_current, COALESCE(is_enabled, 1) AS is_enabled, COALESCE(auto_refresh, 1) AS auto_refresh, vip_type, created_at, updated_at FROM accounts ORDER BY is_current DESC, updated_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_account_flags(mid: str, is_enabled: Optional[int] = None, auto_refresh: Optional[int] = None) -> bool:
    """更新 B站 账号的启用状态或自动刷新开关."""
    conn = get_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    if is_enabled is not None:
        cur.execute("UPDATE accounts SET is_enabled = ?, updated_at = ? WHERE mid = ?", (int(is_enabled), now, str(mid)))
        if int(is_enabled) == 0:
            cur.execute("UPDATE accounts SET is_current = 0 WHERE mid = ?", (str(mid),))
    if auto_refresh is not None:
        cur.execute("UPDATE accounts SET auto_refresh = ?, updated_at = ? WHERE mid = ?", (int(auto_refresh), now, str(mid)))
    conn.commit()
    conn.close()
    return True


# ---------------------------------------------------------------------- 知乎多账号与 Cookie 支持 (知乎 -> db/zhihu/zhihu_auth.db)
def activate_zhihu_session(profile, cookies):
    """Commit identity, credentials and startup state in one transaction inside zhihu_auth.db."""
    now = int(time.time())
    token = profile["url_token"]
    cookie = encrypt_secret("; ".join(f"{k}={v}" for k, v in cookies.items()))
    payload = encrypt_secret(json.dumps({"cookies": cookies, "user_profile": profile, "updated_at": now}))
    conn = get_zhihu_auth_db_connection()
    try:
        with conn:
            conn.execute("UPDATE zhihu_accounts SET is_current = 0")
            conn.execute("""INSERT INTO zhihu_accounts
                (url_token, uname, face, headline, cookie, is_current, is_enabled, auto_refresh, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, 1, 1, ?, ?)
                ON CONFLICT(url_token) DO UPDATE SET uname=excluded.uname, face=excluded.face,
                headline=excluded.headline, cookie=excluded.cookie, is_current=1, is_enabled=1,
                updated_at=excluded.updated_at""",
                (token, profile.get("name", "知乎用户"), profile.get("avatar_url", ""),
                 profile.get("headline", ""), cookie, now, now))
            conn.execute("INSERT OR REPLACE INTO encrypted_sessions VALUES (?, ?, ?)",
                         ("zhihu_active_session", payload, now))
            conn.execute("INSERT OR REPLACE INTO settings VALUES ('zhihu_logged_out', 'false')")
    finally:
        conn.close()


def deactivate_zhihu_session():
    conn = get_zhihu_auth_db_connection()
    try:
        with conn:
            conn.execute("UPDATE zhihu_accounts SET is_current = 0")
            conn.execute("DELETE FROM encrypted_sessions WHERE session_key = 'zhihu_active_session'")
            conn.execute("INSERT OR REPLACE INTO settings VALUES ('zhihu_logged_out', 'true')")
    finally:
        conn.close()


def save_zhihu_account(url_token: str, uname: str, face: str, cookie: str, headline: str = "", is_current: int = 1):
    """保存或更新知乎账号信息."""
    if not url_token:
        return
    conn = get_zhihu_auth_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    if is_current:
        cur.execute("UPDATE zhihu_accounts SET is_current = 0")

    cur.execute("""
    INSERT INTO zhihu_accounts (url_token, uname, face, headline, cookie, is_current, is_enabled, auto_refresh, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
    ON CONFLICT(url_token) DO UPDATE SET
        uname = excluded.uname,
        face = CASE WHEN excluded.face != '' THEN excluded.face ELSE zhihu_accounts.face END,
        headline = CASE WHEN excluded.headline != '' THEN excluded.headline ELSE zhihu_accounts.headline END,
        cookie = excluded.cookie,
        is_current = excluded.is_current,
        updated_at = excluded.updated_at
    """, (str(url_token), uname, face or "", headline or "", encrypt_secret(cookie), int(is_current), now, now))
    conn.commit()
    conn.close()


def get_zhihu_accounts() -> List[dict]:
    """获取所有已保存的知乎账号列表."""
    conn = get_zhihu_auth_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT url_token, uname, face, headline, is_current, COALESCE(is_enabled, 1) AS is_enabled, COALESCE(auto_refresh, 1) AS auto_refresh, created_at, updated_at FROM zhihu_accounts ORDER BY is_current DESC, updated_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _decrypt_zhihu_account_row(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if not row:
        return None
    d = dict(row)
    if d.get("cookie"):
        d["cookie"] = decrypt_secret(d["cookie"])
    return d


def get_zhihu_account_by_token(url_token: str) -> Optional[dict]:
    conn = get_zhihu_auth_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM zhihu_accounts WHERE url_token = ?", (str(url_token),))
    row = cur.fetchone()
    conn.close()
    return _decrypt_zhihu_account_row(row)


def get_current_zhihu_account() -> Optional[dict]:
    conn = get_zhihu_auth_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM zhihu_accounts WHERE is_current = 1 AND COALESCE(is_enabled, 1) = 1 LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return _decrypt_zhihu_account_row(row)


def set_current_zhihu_account(url_token: str) -> bool:
    conn = get_zhihu_auth_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE zhihu_accounts SET is_current = 0")
    cur.execute("UPDATE zhihu_accounts SET is_current = 1, is_enabled = 1, updated_at = ? WHERE url_token = ?", (int(time.time()), str(url_token)))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def remove_zhihu_account(url_token: str) -> bool:
    conn = get_zhihu_auth_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM zhihu_accounts WHERE url_token = ?", (str(url_token),))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def update_zhihu_account_flags(url_token: str, is_enabled: Optional[int] = None, auto_refresh: Optional[int] = None) -> bool:
    conn = get_zhihu_auth_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    if is_enabled is not None:
        cur.execute("UPDATE zhihu_accounts SET is_enabled = ?, updated_at = ? WHERE url_token = ?", (int(is_enabled), now, str(url_token)))
        if int(is_enabled) == 0:
            cur.execute("UPDATE zhihu_accounts SET is_current = 0 WHERE url_token = ?", (str(url_token),))
    if auto_refresh is not None:
        cur.execute("UPDATE zhihu_accounts SET auto_refresh = ?, updated_at = ? WHERE url_token = ?", (int(auto_refresh), now, str(url_token)))
    conn.commit()
    conn.close()
    return True


def _decrypt_bili_account_row(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if not row:
        return None
    d = dict(row)
    if d.get("cookie"):
        d["cookie"] = decrypt_secret(d["cookie"])
    if d.get("refresh_token"):
        d["refresh_token"] = decrypt_secret(d["refresh_token"])
    return d


def get_account_by_mid(mid: str) -> Optional[dict]:
    """根据 MID 获取单个账号完整凭证（自动解密 Cookie 与 refresh_token）."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM accounts WHERE mid = ?", (str(mid),))
    row = cur.fetchone()
    conn.close()
    return _decrypt_bili_account_row(row)


def get_current_account() -> Optional[dict]:
    """获取当前激活账号的完整凭据（自动解密 Cookie 与 refresh_token）."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM accounts WHERE is_current = 1 LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return _decrypt_bili_account_row(row)


def set_current_account(mid: str) -> bool:
    """切换当前激活账号."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE accounts SET is_current = 0")
    cur.execute("UPDATE accounts SET is_current = 1, updated_at = ? WHERE mid = ?", (int(time.time()), str(mid)))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def remove_account(mid: str) -> bool:
    """从本地数据库删除指定账号."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM accounts WHERE mid = ?", (str(mid),))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_all_settings() -> dict:
    """获取所有配置项为字典."""
    res = {}
    for conn in (get_db_connection(), get_zhihu_auth_db_connection()):
        try:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM settings")
            for r in cur.fetchall():
                res[r["key"]] = r["value"]
        finally:
            conn.close()
    return res


def update_settings(settings_dict: dict):
    """批量更新配置项."""
    bconn = get_db_connection()
    zconn = get_zhihu_auth_db_connection()
    try:
        bcur = bconn.cursor()
        zcur = zconn.cursor()
        for k, v in settings_dict.items():
            key_str = str(k)
            target_cur = zcur if key_str.lower().startswith("zhihu") else bcur
            target_cur.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key_str, str(v)))
        bconn.commit()
        zconn.commit()
    finally:
        zconn.close()
        bconn.close()


# ======================================================================
# 知乎多平台扩展表结构与 CRUD 接口 (博主监听 / 收藏夹监听 / 我赞同的帖子)
# ======================================================================

def init_zhihu_tables(db_path: Optional[str] = None):
    """初始化知乎模块相关表结构与索引."""
    target = db_path if db_path else str(_get_zhihu_auth_db_path())
    conn = get_db_connection(target)
    cur = conn.cursor()

    # 1. 知乎监听博主（答主）表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS zhihu_creators (
        url_token TEXT PRIMARY KEY,
        user_id TEXT DEFAULT '',
        name TEXT NOT NULL,
        avatar_url TEXT DEFAULT '',
        headline TEXT DEFAULT '',
        answer_count INTEGER DEFAULT 0,
        articles_count INTEGER DEFAULT 0,
        follower_count INTEGER DEFAULT 0,
        group_name TEXT DEFAULT '默认分组',
        last_sync_time INTEGER DEFAULT 0,
        latest_created INTEGER DEFAULT 0
    )
    """)

    # 2. 知乎监听收藏夹表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS zhihu_collections (
        collection_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        creator_name TEXT DEFAULT '',
        creator_token TEXT DEFAULT '',
        item_count INTEGER DEFAULT 0,
        follower_count INTEGER DEFAULT 0,
        source_type TEXT DEFAULT 'my',
        last_sync_time INTEGER DEFAULT 0,
        latest_created INTEGER DEFAULT 0
    )
    """)

    # 3. 知乎内容库表（支持来自答主 creator / 收藏夹 collection / 我赞同的 upvote / 单链 single）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS zhihu_contents (
        content_id TEXT NOT NULL,
        source_type TEXT NOT NULL DEFAULT 'creator',
        source_id TEXT NOT NULL DEFAULT '',
        content_type TEXT NOT NULL DEFAULT 'answer',
        question_id TEXT DEFAULT '',
        title TEXT NOT NULL,
        excerpt TEXT DEFAULT '',
        content_html TEXT DEFAULT '',
        content_url TEXT DEFAULT '',
        cover_url TEXT DEFAULT '',
        author_name TEXT DEFAULT '',
        author_token TEXT DEFAULT '',
        author_avatar TEXT DEFAULT '',
        voteup_count INTEGER DEFAULT 0,
        comment_count INTEGER DEFAULT 0,
        created_time INTEGER DEFAULT 0,
        updated_time INTEGER DEFAULT 0,
        upvote_time INTEGER DEFAULT 0,
        is_read INTEGER DEFAULT 1,
        download_status TEXT DEFAULT 'none',
        local_file TEXT DEFAULT '',
        is_paid INTEGER DEFAULT 0,
        paid_reason TEXT DEFAULT '',
        updated_at INTEGER DEFAULT 0,
        PRIMARY KEY (content_id, source_type, source_id)
    )
    """)
    for z_col_sql in [
        "ALTER TABLE zhihu_contents ADD COLUMN is_paid INTEGER DEFAULT 0",
        "ALTER TABLE zhihu_contents ADD COLUMN paid_reason TEXT DEFAULT ''",
        "ALTER TABLE zhihu_contents ADD COLUMN paid_check_version INTEGER DEFAULT 0",
    ]:
        try:
            cur.execute(z_col_sql)
        except Exception:
            pass
    cur.execute("CREATE INDEX IF NOT EXISTS idx_zhihu_contents_source ON zhihu_contents(source_type, source_id, created_time DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_zhihu_contents_upvote ON zhihu_contents(source_type, upvote_time DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_zhihu_contents_unread ON zhihu_contents(is_read)")

    # 启动时将知乎遗留的 downloading/pending 状态转为 interrupted（若归档文件已存在则恢复为 success）
    try:
        cur.execute("SELECT content_id, local_file FROM zhihu_contents WHERE download_status IN ('downloading', 'pending')")
        for row in cur.fetchall():
            lf = row["local_file"] or ""
            if lf and os.path.exists(lf) and os.path.getsize(lf) > 0:
                cur.execute("UPDATE zhihu_contents SET download_status = 'success' WHERE content_id = ?", (row["content_id"],))
            else:
                cur.execute("UPDATE zhihu_contents SET download_status = 'interrupted' WHERE content_id = ?", (row["content_id"],))
    except Exception:
        pass

    # 自动修复历史遗留的「待复核 (paid_check_version=0)」状态：
    # 1. 原本已确认为免费 (is_paid=0) 的历史条目直接升级为 paid_check_version=2；
    # 2. 旧版因 relationship.is_authorized=False 误标为 '未授权付费内容' 但本地已存完整正文 (content_html 非空) 的条目，解除误判并升级为 paid_check_version=2。
    try:
        cur.execute("""
            UPDATE zhihu_contents
            SET is_paid = 0,
                paid_reason = '',
                paid_check_version = 2
            WHERE paid_check_version = 0
              AND (
                  is_paid = 0
                  OR (paid_reason = '未授权付费内容' AND length(TRIM(COALESCE(content_html, ''))) > 0)
              )
        """)
    except Exception:
        pass

    conn.commit()
    conn.close()


# Each account has an independent content database under db/zhihu/accounts/;
# unscoped/legacy tables live in db/zhihu/zhihu_auth.db.
_zhihu_scope = ContextVar("zhihu_account_scope", default=None)
_zhihu_db_lock = threading.RLock()
_zhihu_initialized = set()


def zhihu_workspace_token():
    token = _zhihu_scope.get()
    if token is not None:
        return token
    current = get_current_zhihu_account()
    return str((current or {}).get("url_token") or "")


@contextmanager
def zhihu_workspace(token):
    handle = _zhihu_scope.set(str(token or ""))
    try:
        yield
    finally:
        _zhihu_scope.reset(handle)


def _migrate_legacy_zhihu_workspace_if_needed(token: str, target_path: Path) -> None:
    """将无作用域库 (zhihu_auth.db) 中的存量知乎关注、收藏夹与内容无缝迁移至对应账号的独立工作区库."""
    token = (token or "").strip()
    auth_db_path = _get_zhihu_auth_db_path()
    if not token or not auth_db_path.exists():
        return
    try:
        if target_path.resolve() == auth_db_path.resolve():
            return
    except Exception:
        pass

    init_zhihu_tables(str(auth_db_path))
    main_conn = get_zhihu_auth_db_connection()
    try:
        mcur = main_conn.cursor()
        mig_key = f"zhihu_workspace_migrated:{token}"
        mcur.execute("SELECT value FROM settings WHERE key = ?", (mig_key,))
        row = mcur.fetchone()
        if row and row["value"] == "true":
            return

        # 判断旧版知乎数据是否归属于当前 token 账号
        mcur.execute("SELECT value FROM settings WHERE key = 'zhihu_legacy_owner_token'")
        owner_row = mcur.fetchone()
        legacy_owner = (owner_row["value"] if owner_row else "").strip()
        if legacy_owner and legacy_owner != token:
            return

        belongs_to_token = (legacy_owner == token)
        if not belongs_to_token:
            mcur.execute("SELECT value FROM settings WHERE key = 'zhihu_upvote_token'")
            up_row = mcur.fetchone()
            if up_row and (up_row["value"] or "").strip() == token:
                belongs_to_token = True

        if not belongs_to_token:
            try:
                mcur.execute("SELECT 1 FROM zhihu_collections WHERE creator_token = ? LIMIT 1", (token,))
                if mcur.fetchone():
                    belongs_to_token = True
            except Exception:
                pass

        if not belongs_to_token:
            try:
                mcur.execute("SELECT url_token FROM zhihu_accounts ORDER BY created_at ASC")
                acc_rows = mcur.fetchall()
                if len(acc_rows) == 1 and (acc_rows[0]["url_token"] or "").strip() == token:
                    belongs_to_token = True
            except Exception:
                pass

        if not belongs_to_token:
            return

        # 检查是否有需要迁移的数据
        mcur.execute("SELECT * FROM zhihu_creators")
        legacy_creators = [dict(r) for r in mcur.fetchall()]
        mcur.execute("SELECT * FROM zhihu_collections")
        legacy_collections = [dict(r) for r in mcur.fetchall()]
        mcur.execute("SELECT * FROM zhihu_contents")
        legacy_contents = [dict(r) for r in mcur.fetchall()]

        if legacy_creators or legacy_collections or legacy_contents:
            target_conn = get_db_connection(str(target_path))
            try:
                tcur = target_conn.cursor()
                for c in legacy_creators:
                    tcur.execute("""
                    INSERT INTO zhihu_creators
                        (url_token, user_id, name, avatar_url, headline, answer_count, articles_count, follower_count, group_name, last_sync_time, latest_created)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url_token) DO UPDATE SET
                        last_sync_time = MAX(zhihu_creators.last_sync_time, excluded.last_sync_time),
                        latest_created = MAX(zhihu_creators.latest_created, excluded.latest_created)
                    """, (
                        c.get("url_token", ""),
                        c.get("user_id", ""),
                        c.get("name", ""),
                        c.get("avatar_url", ""),
                        c.get("headline", ""),
                        int(c.get("answer_count") or 0),
                        int(c.get("articles_count") or 0),
                        int(c.get("follower_count") or 0),
                        c.get("group_name") or "默认分组",
                        int(c.get("last_sync_time") or 0),
                        int(c.get("latest_created") or 0),
                    ))
                for col in legacy_collections:
                    tcur.execute("""
                    INSERT INTO zhihu_collections
                        (collection_id, title, description, creator_name, creator_token, item_count, follower_count, source_type, last_sync_time, latest_created)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(collection_id) DO UPDATE SET
                        last_sync_time = MAX(zhihu_collections.last_sync_time, excluded.last_sync_time),
                        latest_created = MAX(zhihu_collections.latest_created, excluded.latest_created)
                    """, (
                        col.get("collection_id", ""),
                        col.get("title", ""),
                        col.get("description", ""),
                        col.get("creator_name", ""),
                        col.get("creator_token", ""),
                        int(col.get("item_count") or 0),
                        int(col.get("follower_count") or 0),
                        col.get("source_type") or "my",
                        int(col.get("last_sync_time") or 0),
                        int(col.get("latest_created") or 0),
                    ))
                for item in legacy_contents:
                    tcur.execute("""
                    INSERT OR IGNORE INTO zhihu_contents (
                        content_id, source_type, source_id, content_type, question_id,
                        title, excerpt, content_html, content_url, cover_url,
                        author_name, author_token, author_avatar,
                        voteup_count, comment_count, created_time, updated_time, upvote_time,
                        is_read, download_status, local_file, is_paid, paid_reason, paid_check_version, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        item.get("content_id", ""),
                        item.get("source_type", "creator"),
                        item.get("source_id", ""),
                        item.get("content_type", "answer"),
                        item.get("question_id", ""),
                        item.get("title", ""),
                        item.get("excerpt", ""),
                        item.get("content_html", ""),
                        item.get("content_url", ""),
                        item.get("cover_url", ""),
                        item.get("author_name", ""),
                        item.get("author_token", ""),
                        item.get("author_avatar", ""),
                        int(item.get("voteup_count") or 0),
                        int(item.get("comment_count") or 0),
                        int(item.get("created_time") or 0),
                        int(item.get("updated_time") or 0),
                        int(item.get("upvote_time") or 0),
                        int(item.get("is_read") if item.get("is_read") is not None else 1),
                        item.get("download_status") or "none",
                        item.get("local_file") or "",
                        int(item.get("is_paid") or 0),
                        item.get("paid_reason") or "",
                        int(item.get("paid_check_version") or 0),
                        int(item.get("updated_at") or 0),
                    ))
                target_conn.commit()
            finally:
                target_conn.close()

        # 同步迁移旧版赞同游标配置
        for s_key in ("zhihu_upvote_token", "zhihu_latest_upvote_time"):
            mcur.execute("SELECT value FROM settings WHERE key = ?", (s_key,))
            s_row = mcur.fetchone()
            if s_row and s_row["value"]:
                scoped_key = f"zhihu_account:{token}:{s_key}"
                mcur.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (scoped_key, s_row["value"]),
                )

        mcur.execute(
            "INSERT INTO settings (key, value) VALUES ('zhihu_legacy_owner_token', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (token,),
        )
        mcur.execute(
            "INSERT INTO settings (key, value) VALUES (?, 'true') "
            "ON CONFLICT(key) DO UPDATE SET value = 'true'",
            (mig_key,),
        )
        main_conn.commit()
    except Exception:
        pass
    finally:
        main_conn.close()


def get_zhihu_db_connection():
    token = zhihu_workspace_token()
    if not token:
        auth_path = _get_zhihu_auth_db_path()
        with _zhihu_db_lock:
            if str(auth_path) not in _zhihu_initialized or not auth_path.exists():
                init_zhihu_tables(str(auth_path))
                _zhihu_initialized.add(str(auth_path))
        return get_zhihu_auth_db_connection()
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    path = _get_zhihu_accounts_db_dir() / (digest + ".db")
    with _zhihu_db_lock:
        if str(path) not in _zhihu_initialized or not path.exists():
            init_zhihu_tables(str(path))
            _migrate_legacy_zhihu_workspace_if_needed(token, path)
            init_zhihu_tables(str(path))
            _zhihu_initialized.add(str(path))
    return get_db_connection(str(path))


def get_zhihu_setting(key, default=""):
    token = zhihu_workspace_token()
    return get_setting(f"zhihu_account:{token}:{key}" if token else key, default)


def set_zhihu_setting(key, value):
    token = zhihu_workspace_token()
    set_setting(f"zhihu_account:{token}:{key}" if token else key, value)


def save_zhihu_creator(creator: dict, group_name: str = "默认分组"):
    """新增或更新监听的知乎答主."""
    url_token = (creator.get("url_token") or "").strip()
    if not url_token:
        return
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO zhihu_creators (
        url_token, user_id, name, avatar_url, headline,
        answer_count, articles_count, follower_count, group_name
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(url_token) DO UPDATE SET
        user_id = CASE WHEN excluded.user_id != '' THEN excluded.user_id ELSE zhihu_creators.user_id END,
        name = excluded.name,
        avatar_url = CASE WHEN excluded.avatar_url != '' THEN excluded.avatar_url ELSE zhihu_creators.avatar_url END,
        headline = CASE WHEN excluded.headline != '' THEN excluded.headline ELSE zhihu_creators.headline END,
        answer_count = excluded.answer_count,
        articles_count = excluded.articles_count,
        follower_count = excluded.follower_count,
        group_name = COALESCE(NULLIF(excluded.group_name, ''), zhihu_creators.group_name)
    """, (
        url_token,
        str(creator.get("user_id", "")),
        creator.get("name", url_token),
        creator.get("avatar_url", ""),
        creator.get("headline", ""),
        int(creator.get("answer_count", 0)),
        int(creator.get("articles_count", 0)),
        int(creator.get("follower_count", 0)),
        group_name or creator.get("group_name") or "默认分组",
    ))
    conn.commit()
    conn.close()


def get_zhihu_creators(group_name: Optional[str] = None) -> List[dict]:
    """读取监听中的知乎答主列表，附带未读数与已抓取条数."""
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    if group_name and group_name not in ("all", "全部"):
        cur.execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM zhihu_contents z WHERE z.source_type = 'creator' AND z.source_id = c.url_token) AS cached_count,
               (SELECT COUNT(*) FROM zhihu_contents z WHERE z.source_type = 'creator' AND z.source_id = c.url_token AND z.is_read = 0) AS unread_count
        FROM zhihu_creators c
        WHERE c.group_name = ?
        ORDER BY unread_count DESC, c.last_sync_time DESC, c.name ASC
        """, (group_name,))
    else:
        cur.execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM zhihu_contents z WHERE z.source_type = 'creator' AND z.source_id = c.url_token) AS cached_count,
               (SELECT COUNT(*) FROM zhihu_contents z WHERE z.source_type = 'creator' AND z.source_id = c.url_token AND z.is_read = 0) AS unread_count
        FROM zhihu_creators c
        ORDER BY unread_count DESC, c.last_sync_time DESC, c.name ASC
        """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_zhihu_creator(url_token: str) -> bool:
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM zhihu_creators WHERE url_token = ?", (url_token,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_zhihu_creator_sync_meta(url_token: str) -> Tuple[int, int]:
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT latest_created, last_sync_time FROM zhihu_creators WHERE url_token = ?", (url_token,))
    row = cur.fetchone()
    conn.close()
    if row:
        return int(row["latest_created"] or 0), int(row["last_sync_time"] or 0)
    return 0, 0


def update_zhihu_creator_sync_meta(url_token: str, latest_created: int):
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    cur.execute("""
    UPDATE zhihu_creators
    SET last_sync_time = ?,
        latest_created = MAX(latest_created, ?)
    WHERE url_token = ?
    """, (now, int(latest_created or 0), url_token))
    conn.commit()
    conn.close()


def save_zhihu_collections(collections: List[dict]):
    """批量保存或更新知乎收藏夹列表."""
    if not collections:
        return
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    for col in collections:
        cid = str(col.get("collection_id") or col.get("id") or "").strip()
        if not cid:
            continue
        cur.execute("""
        INSERT INTO zhihu_collections (
            collection_id, title, description, creator_name, creator_token,
            item_count, follower_count, source_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(collection_id) DO UPDATE SET
            title = excluded.title,
            description = CASE WHEN excluded.description != '' THEN excluded.description ELSE zhihu_collections.description END,
            creator_name = CASE WHEN excluded.creator_name != '' THEN excluded.creator_name ELSE zhihu_collections.creator_name END,
            item_count = excluded.item_count,
            follower_count = excluded.follower_count,
            source_type = COALESCE(NULLIF(excluded.source_type, ''), zhihu_collections.source_type)
        """, (
            cid,
            col.get("title", f"收藏夹 {cid}"),
            col.get("description", ""),
            col.get("creator_name", ""),
            col.get("creator_token", ""),
            int(col.get("item_count", 0)),
            int(col.get("follower_count", 0)),
            col.get("source_type", "my"),
        ))
    conn.commit()
    conn.close()


def get_zhihu_collections(source_type: Optional[str] = None) -> List[dict]:
    """获取监听的知乎收藏夹列表，附带未读与缓存数量."""
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    if source_type and source_type not in ("all", "全部"):
        cur.execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM zhihu_contents z WHERE z.source_type = 'collection' AND z.source_id = c.collection_id) AS cached_count,
               (SELECT COUNT(*) FROM zhihu_contents z WHERE z.source_type = 'collection' AND z.source_id = c.collection_id AND z.is_read = 0) AS unread_count
        FROM zhihu_collections c
        WHERE c.source_type = ?
        ORDER BY unread_count DESC, c.last_sync_time DESC, c.collection_id ASC
        """, (source_type,))
    else:
        cur.execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM zhihu_contents z WHERE z.source_type = 'collection' AND z.source_id = c.collection_id) AS cached_count,
               (SELECT COUNT(*) FROM zhihu_contents z WHERE z.source_type = 'collection' AND z.source_id = c.collection_id AND z.is_read = 0) AS unread_count
        FROM zhihu_collections c
        ORDER BY unread_count DESC, c.last_sync_time DESC, c.collection_id ASC
        """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_zhihu_collection(collection_id: str) -> bool:
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM zhihu_collections WHERE collection_id = ?", (str(collection_id),))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_zhihu_collection_sync_meta(collection_id: str) -> Tuple[int, int]:
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT latest_created, last_sync_time FROM zhihu_collections WHERE collection_id = ?", (str(collection_id),))
    row = cur.fetchone()
    conn.close()
    if row:
        return int(row["latest_created"] or 0), int(row["last_sync_time"] or 0)
    return 0, 0


def get_zhihu_collection_existing_ids(collection_id: str) -> set:
    """获取指定知乎收藏夹本地已缓存的全部 content_id 集合，用于差集精确检测新增收藏."""
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT content_id FROM zhihu_contents WHERE source_type = 'collection' AND source_id = ?", (str(collection_id),))
    rows = cur.fetchall()
    conn.close()
    return {str(r["content_id"]) for r in rows if r["content_id"]}


def get_zhihu_contents_meta_map(source_type: str, source_id: str) -> Dict[str, dict]:
    """获取指定监听源下本地已缓存内容的元数据字典（content_id -> {created_time, updated_time, download_status, local_file}）."""
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    cur.execute("""
    SELECT content_id, created_time, updated_time, download_status, local_file
    FROM zhihu_contents
    WHERE source_type = ? AND source_id = ?
    """, (str(source_type), str(source_id)))
    rows = cur.fetchall()
    conn.close()
    return {
        str(r["content_id"]): {
            "created_time": int(r["created_time"] or 0),
            "updated_time": int(r["updated_time"] or 0),
            "download_status": r["download_status"],
            "local_file": r["local_file"]
        }
        for r in rows if r["content_id"]
    }


def update_zhihu_collection_sync_meta(collection_id: str, latest_created: int):
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    cur.execute("""
    UPDATE zhihu_collections
    SET last_sync_time = ?,
        latest_created = MAX(latest_created, ?)
    WHERE collection_id = ?
    """, (now, int(latest_created or 0), str(collection_id)))
    conn.commit()
    conn.close()


def upsert_zhihu_contents(items: List[dict], is_read_default: int = 1) -> int:
    """批量插入或更新知乎回答/文章/视频记录，保留已有的归档状态与本地路径."""
    if not items:
        return 0
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    count = 0

    for item in items:
        cid = str(item.get("content_id") or "").strip()
        if not cid:
            continue
        source_type = item.get("source_type") or "creator"
        source_id = str(item.get("source_id") or item.get("author_token") or "")
        is_read_val = int(item.get("is_read")) if item.get("is_read") is not None else is_read_default

        # 若该 content_id 已在其它源（如点赞/答主）中完成离线归档，自动共享归档状态与本地文件
        cur.execute("SELECT download_status, local_file FROM zhihu_contents WHERE content_id = ? AND download_status = 'success' LIMIT 1", (cid,))
        arch_row = cur.fetchone()
        dl_status = arch_row["download_status"] if arch_row else item.get("download_status", "none")
        loc_file = arch_row["local_file"] if arch_row else item.get("local_file", "")

        cur.execute("""
        INSERT INTO zhihu_contents (
            content_id, source_type, source_id, content_type, question_id,
            title, excerpt, content_html, content_url, cover_url,
            author_name, author_token, author_avatar,
            voteup_count, comment_count, created_time, updated_time, upvote_time,
            is_read, download_status, local_file, is_paid, paid_reason, paid_check_version, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(content_id, source_type, source_id) DO UPDATE SET
            title = excluded.title,
            excerpt = CASE WHEN excluded.excerpt != '' THEN excluded.excerpt ELSE zhihu_contents.excerpt END,
            content_html = CASE WHEN length(excluded.content_html) > length(zhihu_contents.content_html) THEN excluded.content_html ELSE zhihu_contents.content_html END,
            content_url = excluded.content_url,
            cover_url = CASE WHEN excluded.cover_url != '' THEN excluded.cover_url ELSE zhihu_contents.cover_url END,
            author_name = excluded.author_name,
            author_token = excluded.author_token,
            author_avatar = CASE WHEN excluded.author_avatar != '' THEN excluded.author_avatar ELSE zhihu_contents.author_avatar END,
            voteup_count = excluded.voteup_count,
            comment_count = excluded.comment_count,
            updated_time = MAX(zhihu_contents.updated_time, excluded.updated_time),
            is_read = CASE WHEN excluded.updated_time > zhihu_contents.updated_time THEN 0 ELSE zhihu_contents.is_read END,
            upvote_time = MAX(zhihu_contents.upvote_time, excluded.upvote_time),
            download_status = CASE WHEN zhihu_contents.download_status = 'success' THEN 'success' ELSE excluded.download_status END,
            local_file = CASE WHEN zhihu_contents.local_file != '' THEN zhihu_contents.local_file ELSE excluded.local_file END,
            is_paid = excluded.is_paid,
            paid_reason = excluded.paid_reason,
            paid_check_version = excluded.paid_check_version,
            updated_at = excluded.updated_at
        """, (
            cid,
            source_type,
            source_id,
            item.get("content_type", "answer"),
            str(item.get("question_id", "")),
            item.get("title", ""),
            item.get("excerpt", ""),
            item.get("content_html", ""),
            item.get("content_url", ""),
            item.get("cover_url", ""),
            item.get("author_name", ""),
            item.get("author_token", ""),
            item.get("author_avatar", ""),
            int(item.get("voteup_count", 0)),
            int(item.get("comment_count", 0)),
            int(item.get("created_time", 0)),
            int(item.get("updated_time", 0)),
            int(item.get("upvote_time", 0)),
            is_read_val,
            dl_status,
            loc_file,
            1 if item.get("is_paid") else 0,
            str(item.get("paid_reason") or ""),
            int(item.get("paid_check_version") or 0),
            now,
        ))
        count += 1

    conn.commit()
    conn.close()
    return count


def query_zhihu_contents(
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    content_type: Optional[str] = None,
    only_unread: bool = False,
    only_archived: bool = False,
    search_kw: Optional[str] = None,
    limit: int = 300,
    offset: int = 0,
) -> List[dict]:
    """多维筛选查询知乎内容列表."""
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    conditions = []
    params: list = []

    if source_type and source_type != "all":
        conditions.append("source_type = ?")
        params.append(source_type)
    if source_id and source_id != "all":
        conditions.append("source_id = ?")
        params.append(str(source_id))
    if content_type and content_type != "all":
        conditions.append("content_type = ?")
        params.append(content_type)
    if only_unread:
        conditions.append("is_read = 0")
    if only_archived:
        conditions.append("download_status = 'success'")
    if search_kw and search_kw.strip():
        kw = f"%{search_kw.strip()}%"
        conditions.append("(title LIKE ? OR excerpt LIKE ? OR author_name LIKE ? OR content_id LIKE ?)")
        params.extend([kw, kw, kw, kw])

    where_sql = " WHERE " + " AND ".join(conditions) if conditions else ""
    order_col = "upvote_time DESC, created_time DESC" if source_type == "upvote" else "created_time DESC, updated_time DESC"
    sql = f"""
    SELECT *
    FROM zhihu_contents
    {where_sql}
    ORDER BY {order_col}
    LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_zhihu_content_detail(content_id: str) -> Optional[dict]:
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM zhihu_contents WHERE content_id = ? ORDER BY length(content_html) DESC LIMIT 1", (str(content_id),))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_zhihu_archive_status(
    content_id: str,
    status: str,
    local_file: str = "",
    content_html: Optional[str] = None,
):
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    now = int(time.time())
    if not local_file and status in ("none", "failed", "cancelled"):
        cur.execute("SELECT local_file FROM zhihu_contents WHERE content_id = ? AND local_file != '' LIMIT 1", (str(content_id),))
        row = cur.fetchone()
        existing_file = (row["local_file"] or "") if row else ""
        if existing_file and os.path.exists(existing_file) and os.path.getsize(existing_file) > 0:
            status = "success"
    if content_html:
        cur.execute("""
        UPDATE zhihu_contents
        SET download_status = ?, local_file = ?, content_html = ?, updated_at = ?
        WHERE content_id = ?
        """, (status, local_file, content_html, now, str(content_id)))
    else:
        cur.execute("""
        UPDATE zhihu_contents
        SET download_status = ?, local_file = CASE WHEN ? != '' THEN ? ELSE local_file END, updated_at = ?
        WHERE content_id = ?
        """, (status, local_file, local_file, now, str(content_id)))
    conn.commit()
    conn.close()


def update_zhihu_access_info(content_id, item):
    conn = get_zhihu_db_connection()
    try:
        with conn:
            conn.execute("""UPDATE zhihu_contents SET is_paid=?, paid_reason=?, paid_check_version=?,
                content_html=CASE WHEN ? != '' THEN ? ELSE content_html END WHERE content_id=?""",
                (int(bool(item.get("is_paid"))), item.get("paid_reason", ""), item.get("paid_check_version", 0),
                 item.get("content_html", ""), item.get("content_html", ""), str(content_id)))
    finally:
        conn.close()


def mark_zhihu_contents_read(
    content_id: Optional[str] = None,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
) -> int:
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    if content_id:
        cur.execute("UPDATE zhihu_contents SET is_read = 1 WHERE content_id = ? AND is_read = 0", (str(content_id),))
    elif source_type and source_id:
        cur.execute(
            "UPDATE zhihu_contents SET is_read = 1 WHERE source_type = ? AND source_id = ? AND is_read = 0",
            (source_type, str(source_id)),
        )
    elif source_type:
        cur.execute("UPDATE zhihu_contents SET is_read = 1 WHERE source_type = ? AND is_read = 0", (source_type,))
    else:
        cur.execute("UPDATE zhihu_contents SET is_read = 1 WHERE is_read = 0")
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected


def get_zhihu_summary_counts() -> dict:
    """统计知乎各模块的未读数与总缓存数，供最左侧导航树实时显示徽章."""
    conn = get_zhihu_db_connection()
    cur = conn.cursor()
    res = {
        "creator_unread": 0,
        "collection_unread": 0,
        "upvote_unread": 0,
        "total_unread": 0,
        "creator_total": 0,
        "collection_total": 0,
        "upvote_total": 0,
        "archived_total": 0,
    }
    try:
        cur.execute("""
        SELECT source_type,
               COUNT(*) AS total_cnt,
               SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS unread_cnt,
               SUM(CASE WHEN download_status = 'success' THEN 1 ELSE 0 END) AS arch_cnt
        FROM zhihu_contents
        GROUP BY source_type
        """)
        for r in cur.fetchall():
            st = r["source_type"]
            t_cnt = int(r["total_cnt"] or 0)
            u_cnt = int(r["unread_cnt"] or 0)
            a_cnt = int(r["arch_cnt"] or 0)
            res["total_unread"] += u_cnt
            res["archived_total"] += a_cnt
            if st == "creator":
                res["creator_unread"] = u_cnt
                res["creator_total"] = t_cnt
            elif st == "collection":
                res["collection_unread"] = u_cnt
                res["collection_total"] = t_cnt
            elif st == "upvote":
                res["upvote_unread"] = u_cnt
                res["upvote_total"] = t_cnt
    except Exception:
        pass
    finally:
        conn.close()
    return res
