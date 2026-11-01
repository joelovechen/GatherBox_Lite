# -*- coding: utf-8 -*-
"""会话持久化：Cookie 和 refresh_token 统一通过加密形式持久化至 SQLite 数据库 (bili_data.db).

安全特性：
1. 不再向磁盘写入任何明文 `session.json` 或 `cookies.txt` 文件。
2. 活跃会话通过 `utils.crypto` (Windows DPAPI / PBKDF2-HMAC-SHA256) 加密后存入
   SQLite `encrypted_sessions` 表 (`session_key = 'bili_active_session'`)。
3. 若检测到旧版本遗留的 `session.json` 或 `cookies.txt` 明文文件，自动迁移入库并立即从磁盘删除。
"""

import json
import os
import sys
from utils.common_util import now_ts
from utils.paths import default_data_dir


def _resolve_storage_dir():
    env_dir = os.environ.get("BILI_DATA_DIR")
    if env_dir:
        if not os.path.exists(env_dir):
            os.makedirs(env_dir, exist_ok=True)
        return env_dir
    download_dir = default_data_dir()
    if os.path.exists(download_dir):
        return download_dir
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    if "NUITKA_ONEFILE_DIRECTORY" in os.environ:
        return os.path.dirname(os.path.abspath(sys.argv[0]))
    src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "Temp" in src_root or "temp" in src_root:
        return os.getcwd()
    os.makedirs(download_dir, exist_ok=True)
    return download_dir


ROOT = _resolve_storage_dir()
SESSION_FILE = os.path.join(ROOT, 'session.json')
COOKIE_FILE = os.path.join(ROOT, 'cookies.txt')


def set_storage_dir(dir_path: str):
    global ROOT, SESSION_FILE, COOKIE_FILE
    if not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
    ROOT = dir_path
    SESSION_FILE = os.path.join(ROOT, 'session.json')
    COOKIE_FILE = os.path.join(ROOT, 'cookies.txt')


def _cleanup_legacy_plaintext_files():
    """清理磁盘上可能遗留的明文 session.json 与 cookies.txt."""
    for fpath in (SESSION_FILE, COOKIE_FILE):
        if fpath and os.path.exists(fpath):
            try:
                os.remove(fpath)
            except OSError:
                pass


def load_session(path: str = '') -> dict:
    """从 SQLite 加密会话表读取当前 B站 会话（若存在旧版明文文件则自动迁移入库并删除明文文件）.

    :param path: 自定义路径（仅用于外部测试工具显式传入自定义路径，默认空字符串走 SQLite 加密存储）.
    :return: {"cookie","refresh_token","mid","updated_at"}，读不到时返回空 dict.
    """
    if path:
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get('cookie'):
                    return data
            except Exception:
                pass
        return {}

    from utils import db

    # 1. 若磁盘上仍有旧版明文 session.json 或 cookies.txt，先迁入加密数据库并销毁明文文件
    migrated_payload = None
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get('cookie'):
                migrated_payload = {
                    'cookie': str(data.get('cookie', '')),
                    'refresh_token': str(data.get('refresh_token', '')),
                    'mid': str(data.get('mid', '')),
                    'updated_at': int(data.get('updated_at') or now_ts()),
                }
        except Exception:
            pass
    if not migrated_payload and os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, encoding='utf-8') as f:
                cookie = f.read().strip()
            if cookie:
                migrated_payload = {'cookie': cookie, 'refresh_token': '', 'mid': '', 'updated_at': now_ts()}
        except Exception:
            pass

    if migrated_payload:
        db.save_encrypted_session_blob("bili_active_session", migrated_payload)
        _cleanup_legacy_plaintext_files()
        return migrated_payload

    _cleanup_legacy_plaintext_files()

    # 2. 从 SQLite encrypted_sessions 表读取解密后的会话
    blob = db.load_encrypted_session_blob("bili_active_session")
    if isinstance(blob, dict) and blob.get('cookie'):
        return blob

    # 3. 兜底：若 encrypted_sessions 为空但 accounts 表有当前激活账号，自动恢复并同步
    cur_acc = db.get_current_account()
    if cur_acc and cur_acc.get('cookie'):
        fallback_payload = {
            'cookie': str(cur_acc.get('cookie', '')),
            'refresh_token': str(cur_acc.get('refresh_token', '')),
            'mid': str(cur_acc.get('mid', '')),
            'updated_at': int(cur_acc.get('updated_at') or now_ts()),
        }
        db.save_encrypted_session_blob("bili_active_session", fallback_payload)
        return fallback_payload

    return {}


def save_session(auth, path: str = '') -> str:
    """将 auth 的 Cookie 与 refresh_token 加密写入 SQLite 数据库（绝不落盘明文文件）.

    :param auth: BiliAuth object.
    :param path: 自定义路径（默认空字符串走 SQLite 加密存储）.
    :return: 存储标识字符串.
    """
    payload = {
        'cookie': auth.cookies_str,
        'refresh_token': getattr(auth, 'refresh_token', '') or '',
        'mid': auth.mid,
        'updated_at': now_ts(),
    }
    if path:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path

    from utils import db
    db.save_encrypted_session_blob("bili_active_session", payload)
    _cleanup_legacy_plaintext_files()
    return "sqlite://bili_data.db/encrypted_sessions/bili_active_session"


def clear_session() -> None:
    """清除 SQLite 中的 B站 活跃加密会话以及任何遗留明文文件."""
    from utils import db
    db.delete_encrypted_session_blob("bili_active_session")
    _cleanup_legacy_plaintext_files()


def has_session(path: str = '') -> bool:
    return bool(load_session(path).get('cookie'))
