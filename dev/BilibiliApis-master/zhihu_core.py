# -*- coding: utf-8 -*-
"""
知乎核心引擎模块 (dev/BilibiliApis-master/zhihu_core.py)
包含：
1. 纯 Python 原生 x-zse-96 / x-zst-81 签名算法（1:1 移植自 MediaCrawler libs/zhihu.js，零 Node/execjs 依赖）
2. 知乎扫码登录与 Cookie 会话管理 (ZhihuAuthManager)，基于 curl_cffi Chrome 指纹伪装
3. 知乎三大监听与采集客户端 (ZhihuClient)：
   - 博主（答主）回答 / 专栏文章 / 视频监听与分页抓取
   - 收藏夹（我创建的 / 我关注的 / 外部公开收藏夹）监听与分页抓取
   - 自己点赞（赞同）的帖子动态流监听与分页抓取 (/api/v4/members/{url_token}/activities)
   - 单链接（回答 / 专栏文章 / 问题）解析采集
4. 知乎富文本离线归档器 (ZhihuArchiver)：
   - 生成带 YAML Front-Matter 的 Markdown (content.md)
   - 生成独立样式离线 HTML (content.html)
   - 自动下载正文配图至本地 images/ 目录并替换为相对路径
   - 保存完整元数据快照 (info.json)
"""

import base64
import copy
import hashlib
import html as html_lib
import io
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import qrcode
from curl_cffi import requests as cffi_requests

from utils.logger import app_logger, log_http_exchange
from utils.paths import default_data_dir
from utils.rate_limiter import zhihu_limiter

ZHIHU_URL = "https://www.zhihu.com"
ZHIHU_ZHUANLAN_URL = "https://zhuanlan.zhihu.com"
IMPERSONATE = "chrome131"
ZHIHU_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

X_ZSE_93 = "101_3_3.0"
X_ZST_81 = (
    "3_2.0aR_sn77yn6O92wOB8hPZnQr0EMYxc4f18wNBUgpTQ6nxERFZfTY0-4Lm-h3_tufIwJS8gcxTg"
    "JS_AuPZNcXCTwxI78YxEM20s4PGDwN8gGcYAupMWufIoLVqr4gxrRPOI0cY7HL8qun9g93mFukyigc"
    "mebS_FwOYPRP0E4rZUrN9DDom3hnynAUMnAVPF_PhaueTFH9fQL39OCCqYTxfb0rfi9wfPhSM6vxGD"
    "Jo_rBHpQGNmBBLqPJHK2_w8C9eTVMO9Z9NOrMtfhGH_DgpM-BNM1DOxScLG3gg1Hre1FCXKQcXKkrS"
    "L1r9GWDXMk8wqBLNmbRH96BtOFqVZ7UYG3gC8D9cMS7Y9UrHLVCLZPJO8_CL_6GNCOg_zhJS8PbXmG"
    "TcBpgxfkieOPhNfthtf2gC_qD3YOce8nCwG2uwBOqeMoML9NBC1xb9yk6SuJhHLK7SM6LVfCve_3vL"
    "KlqcL6TxL_UosDvHLxrHmWgxBQ8Xs"
)

_INIT_STR = "6fpLRqJO8M/c3jnYxFkUVC4ZIG12SiH=5v0mXDazWBTsuw7QetbKdoPyAl+hN9rgE"
_ZK = [
    1170614578, 1024848638, 1413669199, -343334464, -766094290, -1373058082,
    -143119608, -297228157, 1933479194, -971186181, -406453910, 460404854,
    -547427574, -1891326262, -1679095901, 2119585428, -2029270069, 2035090028,
    -1521520070, -5587175, -77751101, -2094365853, -1243052806, 1579901135,
    1321810770, 456816404, -1391643889, -229302305, 330002838, -788960546,
    363569021, -1947871109,
]
_ZB = [
    20, 223, 245, 7, 248, 2, 194, 209, 87, 6, 227, 253, 240, 128, 222, 91, 237, 9,
    125, 157, 230, 93, 252, 205, 90, 79, 144, 199, 159, 197, 186, 167, 39, 37, 156,
    198, 38, 42, 43, 168, 217, 153, 15, 103, 80, 189, 71, 191, 97, 84, 247, 95, 36,
    69, 14, 35, 12, 171, 28, 114, 178, 148, 86, 182, 32, 83, 158, 109, 22, 255, 94,
    238, 151, 85, 77, 124, 254, 18, 4, 26, 123, 176, 232, 193, 131, 172, 143, 142,
    150, 30, 10, 146, 162, 62, 224, 218, 196, 229, 1, 192, 213, 27, 110, 56, 231,
    180, 138, 107, 242, 187, 54, 120, 19, 44, 117, 228, 215, 203, 53, 239, 251, 127,
    81, 11, 133, 96, 204, 132, 41, 115, 73, 55, 249, 147, 102, 48, 122, 145, 106,
    118, 74, 190, 29, 16, 174, 5, 177, 129, 63, 113, 99, 31, 161, 76, 246, 34, 211,
    13, 60, 68, 207, 160, 65, 111, 82, 165, 67, 169, 225, 57, 112, 244, 155, 51, 236,
    200, 233, 58, 61, 47, 100, 137, 185, 64, 17, 70, 234, 163, 219, 108, 170, 166,
    59, 149, 52, 105, 24, 212, 78, 173, 45, 0, 116, 226, 119, 136, 206, 135, 175,
    195, 25, 92, 121, 208, 126, 139, 3, 75, 141, 21, 130, 98, 241, 40, 154, 66, 184,
    49, 181, 46, 243, 88, 101, 183, 8, 23, 72, 188, 104, 179, 210, 134, 250, 201,
    164, 89, 216, 202, 220, 50, 221, 152, 140, 33, 235, 214,
]
_ARRAY_OFFSET = [48, 53, 57, 48, 53, 51, 102, 55, 100, 49, 53, 101, 48, 49, 100, 55]


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (name or "").strip())
    return cleaned.strip(" ._") or "untitled"


def _to_int32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if (x & 0x80000000) else x


def _unpack_u32(e: int, t: List[int], n: int) -> None:
    u = e & 0xFFFFFFFF
    t[n] = (u >> 24) & 0xFF
    t[n + 1] = (u >> 16) & 0xFF
    t[n + 2] = (u >> 8) & 0xFF
    t[n + 3] = u & 0xFF


def _rotl32(e: int, bits: int) -> int:
    u = e & 0xFFFFFFFF
    return _to_int32(((u << bits) & 0xFFFFFFFF) | (u >> (32 - bits)))


def _pack_u32(e: List[int], t: int) -> int:
    return _to_int32(
        ((e[t] & 0xFF) << 24)
        | ((e[t + 1] & 0xFF) << 16)
        | ((e[t + 2] & 0xFF) << 8)
        | (e[t + 3] & 0xFF)
    )


def _tau_l(e: int) -> int:
    t = [0, 0, 0, 0]
    _unpack_u32(e, t, 0)
    n = [
        _ZB[t[0] & 0xFF],
        _ZB[t[1] & 0xFF],
        _ZB[t[2] & 0xFF],
        _ZB[t[3] & 0xFF],
    ]
    r = _pack_u32(n, 0)
    return _to_int32(r ^ _rotl32(r, 2) ^ _rotl32(r, 10) ^ _rotl32(r, 18) ^ _rotl32(r, 24))


def _array_0_16_offset(e: List[int]) -> List[int]:
    t = [0] * 16
    n = [0] * 36
    n[0] = _pack_u32(e, 0)
    n[1] = _pack_u32(e, 4)
    n[2] = _pack_u32(e, 8)
    n[3] = _pack_u32(e, 12)
    for r in range(32):
        o = _tau_l(_to_int32(n[r + 1] ^ n[r + 2] ^ n[r + 3] ^ _ZK[r]))
        n[r + 4] = _to_int32(n[r] ^ o)
    _unpack_u32(n[35], t, 0)
    _unpack_u32(n[34], t, 4)
    _unpack_u32(n[33], t, 8)
    _unpack_u32(n[32], t, 12)
    return t


def _array_16_48_offset(e: List[int], t: List[int]) -> List[int]:
    n: List[int] = []
    r = len(e)
    idx = 0
    while r > 0:
        o = e[16 * idx : 16 * (idx + 1)]
        a = [(o[c] ^ t[c]) for c in range(16)]
        t = _array_0_16_offset(a)
        n.extend(t)
        idx += 1
        r -= 16
    return n


def _encode_0_16(array_0_16: List[int]) -> List[int]:
    result = [(array_0_16[i] ^ _ARRAY_OFFSET[i] ^ 42) for i in range(len(array_0_16))]
    return _array_0_16_offset(result)


def _encode_3bytes(ar: List[int]) -> List[int]:
    b = (ar[1] & 0xFF) << 8
    c = (ar[0] & 0xFF) | b
    d = (ar[2] & 0xFF) << 16
    e = (c | d) & 0xFFFFFFFF
    result_array = [e & 63]
    x6 = 6
    while len(result_array) < 4:
        result_array.append((e >> x6) & 63)
        x6 += 6
    return result_array


def _get_zse_96(encode_md5: str, rand_byte: Optional[int] = None) -> str:
    init_array = [ord(ch) for ch in encode_md5]
    init_array.insert(0, 0)
    init_array.insert(0, rand_byte if rand_byte is not None else random.randint(0, 126))
    while len(init_array) < 48:
        init_array.append(14)

    arr_0_16 = _encode_0_16(init_array[:16])
    arr_16_48 = _array_16_48_offset(init_array[16:48], arr_0_16)
    arr_res = arr_0_16 + arr_16_48

    for idx in range(47, -1, -4):
        arr_res[idx] ^= 58
    arr_res.reverse()

    result_indices: List[int] = []
    for j in range(3, len(arr_res) + 1, 3):
        result_indices.extend(_encode_3bytes(arr_res[j - 3 : j]))

    return "2.0_" + "".join(_INIT_STR[idx] for idx in result_indices)


def sign_zhihu_request(uri_with_query: str, d_c0: str, rand_byte: Optional[int] = None) -> Dict[str, str]:
    """
    计算知乎 V4 接口所需的 x-zse-93 / x-zst-81 / x-zse-96 签名头。
    """
    raw_str = "+".join([X_ZSE_93, uri_with_query, d_c0 or "", X_ZST_81])
    md5_hex = hashlib.md5(raw_str.encode("utf-8")).hexdigest()
    return {
        "x-zse-93": X_ZSE_93,
        "x-zst-81": X_ZST_81,
        "x-zse-96": _get_zse_96(md5_hex, rand_byte=rand_byte),
    }


# ==================== 2. 轻量 HTML 解析与图片提取（零外部 bs4 依赖） ====================

def _coerce_html_str(raw_val: Any) -> str:
    if not raw_val:
        return ""
    if isinstance(raw_val, str):
        return raw_val
    if isinstance(raw_val, list):
        parts: List[str] = []
        for blk in raw_val:
            if isinstance(blk, str):
                parts.append(blk)
            elif isinstance(blk, dict):
                btype = str(blk.get("type", "")).lower()
                if btype == "image":
                    img_u = blk.get("url") or blk.get("original_url") or ""
                    if img_u:
                        parts.append(f'<p><img src="{img_u}" /></p>')
                else:
                    txt = blk.get("content") or blk.get("text") or blk.get("title") or ""
                    if isinstance(txt, str) and txt:
                        parts.append(txt if "<" in txt else f"<p>{txt}</p>")
        return "".join(parts)
    return str(raw_val)


def strip_html_tags(raw_html: Any) -> str:
    raw_html = _coerce_html_str(raw_html)
    if not raw_html:
        return ""
    text = re.sub(r"<noscript>.*?</noscript>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script.*?>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_js_initial_data(html_text: str) -> Dict[str, Any]:
    if not html_text:
        return {}
    m = re.search(
        r'<script[^>]*id=["\']js-initialData["\'][^>]*>(.*?)</script>',
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            return {}
    return {}


# ==================== 3. 知乎扫码登录与 Cookie 会话管理 ====================

class ZhihuAuthManager:
    """
    管理知乎登录凭证 (d_c0, z_c0, _xsrf)，提供：
    1. 自动初始化设备 Cookie (d_c0)
    2. 官方扫码登录申请与轮询 (/api/v3/account/api/login/qrcode)
    3. 真实浏览器窗口扫码/过安全验证登录 (Playwright Edge/Chrome)
    4. 手动粘贴 Cookie 字符串导入兜底
    5. 当前登录用户身份验证 (/api/v4/me)
    """

    def __init__(self, cookie_file: Optional[str] = None):
        self.cookie_file = cookie_file or str(Path(default_data_dir()) / "zhihu_session.json")
        self._lock = threading.RLock()
        self._commit_lock = threading.RLock()
        self._browser_cancel = threading.Event()
        self._login_epoch = 0
        self._account_cancellations = {}
        self.verification_state = "pending"
        self._verification_running = False
        self._verification_next = 0.0
        self.cookies_dict: Dict[str, str] = {}
        self._qr_cookies: Dict[str, str] = {}
        self.user_profile: Optional[Dict[str, Any]] = None
        self.qr_token: Optional[str] = None
        self._auth_session: Optional[cffi_requests.Session] = None
        self._browser_login_state: Dict[str, Any] = {
            "status": "idle",
            "message": "",
            "updated_at": 0,
        }
        self._load_cookies()

    def set_storage_dir(self, data_dir: str) -> None:
        self.cookie_file = str(Path(data_dir) / "zhihu_session.json")
        self._load_cookies()

    def _new_session(self) -> cffi_requests.Session:
        return cffi_requests.Session(impersonate=IMPERSONATE)

    def _get_auth_session(self, reset: bool = False) -> cffi_requests.Session:
        if reset or self._auth_session is None:
            self._auth_session = cffi_requests.Session(impersonate=IMPERSONATE)
        return self._auth_session

    def _load_cookies(self) -> None:
        try:
            from utils import db
            if db.get_setting("zhihu_logged_out", "false") == "true":
                self.cookies_dict = {}
                self.user_profile = None
                return
            # 1. 若磁盘上存在旧版明文 zhihu_session.json，先迁入 SQLite 加密表并删除明文文件
            if os.path.exists(self.cookie_file):
                try:
                    with open(self.cookie_file, "r", encoding="utf-8") as f:
                        legacy_data = json.load(f)
                    if isinstance(legacy_data, dict):
                        db.save_encrypted_session_blob("zhihu_active_session", legacy_data)
                except Exception:
                    pass
                try:
                    os.remove(self.cookie_file)
                except OSError:
                    pass

            # 2. 从 SQLite encrypted_sessions 表读取并解密知乎会话
            data = db.load_encrypted_session_blob("zhihu_active_session")
            if not isinstance(data, dict) or not (data.get("cookies") or {}).get("z_c0") or not data.get("user_profile"):
                cur_acc = db.get_current_zhihu_account()
                if cur_acc and cur_acc.get("is_enabled", 1) and cur_acc.get("cookie"):
                    cookie_str = (cur_acc.get("cookie") or "").strip()
                    parsed_cookies = {
                        k.strip(): v.strip()
                        for part in cookie_str.split(";")
                        if "=" in part
                        for k, v in [part.strip().split("=", 1)]
                        if k.strip() and v.strip() and k.strip() != "sec_token"
                    }
                    if parsed_cookies.get("z_c0"):
                        data = {
                            "cookies": parsed_cookies,
                            "user_profile": {
                                "url_token": cur_acc.get("url_token", ""),
                                "name": cur_acc.get("uname") or cur_acc.get("url_token", ""),
                                "avatar_url": cur_acc.get("face", ""),
                                "headline": cur_acc.get("headline", ""),
                            },
                        }
                        db.save_encrypted_session_blob("zhihu_active_session", data)
            elif isinstance(data, dict) and (data.get("user_profile") or {}).get("url_token") and not db.get_current_zhihu_account():
                up = data["user_profile"]
                tok = str(up.get("url_token") or "").strip()
                acc = db.get_zhihu_account_by_token(tok) if tok else None
                raw_ck = data.get("cookies") or {}
                if tok and raw_ck.get("z_c0") and (not acc or acc.get("is_enabled", 1)):
                    ck_str = "; ".join(f"{k}={v}" for k, v in raw_ck.items() if v and k != "sec_token")
                    db.save_zhihu_account(
                        url_token=tok,
                        uname=up.get("name") or tok,
                        face=up.get("avatar_url") or "",
                        cookie=ck_str,
                        headline=up.get("headline") or "",
                        is_current=1,
                    )
            if isinstance(data, dict):
                self.cookies_dict = {
                    str(k): str(v)
                    for k, v in (data.get("cookies") or data).items()
                    if v and not isinstance(v, (dict, list)) and str(k) != "sec_token"
                }
                self.user_profile = data.get("user_profile")
                token = (self.user_profile or {}).get("url_token")
                account = db.get_zhihu_account_by_token(token) if token else None
                if account and not account.get("is_enabled", 1):
                    self.cookies_dict = {}
                    self.user_profile = None
        except Exception as e:
            app_logger.warning("[ZhihuAuth] 读取数据库加密知乎会话失败: %s", e)

    def _save_cookies(self) -> None:
        try:
            from utils import db
            self.cookies_dict.pop("sec_token", None)
            payload = {
                "cookies": self.cookies_dict,
                "user_profile": self.user_profile,
                "updated_at": int(time.time()),
            }
            db.save_encrypted_session_blob("zhihu_active_session", payload)
            if os.path.exists(self.cookie_file):
                try:
                    os.remove(self.cookie_file)
                except OSError:
                    pass
        except Exception as e:
            app_logger.warning("[ZhihuAuth] 保存加密知乎会话至数据库失败: %s", e)

    def ensure_dc0(self) -> str:
        """确保拥有签名必需的 d_c0 设备 Cookie；直接调用 POST /udid 避免触发 /signin 302 sec_token"""
        self.cookies_dict.pop("sec_token", None)
        dc0 = self.cookies_dict.get("d_c0")
        if dc0:
            return dc0

        sess = self._get_auth_session()
        try:
            t0 = time.time()
            udid_url = f"{ZHIHU_URL}/udid"
            req_headers = {
                "User-Agent": ZHIHU_UA,
                "Referer": f"{ZHIHU_URL}/signin",
                "Origin": ZHIHU_URL,
                "x-requested-with": "fetch",
            }
            udid_resp = sess.post(udid_url, headers=req_headers, timeout=10)
            dur = (time.time() - t0) * 1000.0
            for k, v in udid_resp.cookies.items():
                if v and k not in ("sec_token", "z_c0"):
                    self.cookies_dict[k] = v
            log_http_exchange(
                platform="ZhihuAuth",
                method="POST",
                url=udid_url,
                status_code=udid_resp.status_code,
                req_headers=req_headers,
                resp_cookies=dict(udid_resp.cookies),
                resp_body=udid_resp.text[:300],
                duration_ms=dur,
            )
        except Exception as e:
            app_logger.warning("[ZhihuAuth] 自动申请 d_c0 异常: %s", e)

        if "d_c0" not in self.cookies_dict:
            rand_id = base64.b64encode(os.urandom(24)).decode("ascii").rstrip("=")
            self.cookies_dict["d_c0"] = f"{rand_id}|{int(time.time())}"

        self._save_cookies()
        return self.cookies_dict["d_c0"]

    def get_cookie_header(self) -> str:
        return "; ".join(
            f"{k}={v}" for k, v in self.cookies_dict.items() if v and k != "sec_token"
        )

    @property
    def is_login(self) -> bool:
        return bool(self.cookies_dict.get("z_c0"))

    def snapshot(self, account_token: Optional[str] = None):
        """Independent request credentials bound to account_token; never persist response cookies to the active account."""
        result = copy.copy(self)
        result._lock = threading.RLock()
        result._auth_session = None
        result._save_cookies = lambda: None
        result._persist_invalidation = lambda token: None
        result.revoke_account_tasks = lambda token: None

        active_token = str((self.user_profile or {}).get("url_token") or "")
        if account_token is None or (account_token != "" and account_token == active_token and self.is_login):
            if account_token:
                from utils import db
                acc = db.get_zhihu_account_by_token(account_token)
                if acc and not acc.get("is_enabled", 1):
                    raise RuntimeError(f"知乎账号 {account_token} 已停用，无法执行任务")
            token = account_token if account_token is not None else active_token
            revoked = self._account_cancellations.setdefault(token, threading.Event())
            if account_token and revoked.is_set():
                raise RuntimeError(f"知乎账号 {account_token} 已退出或移除，无法执行任务")
            result.cookies_dict = dict(self.cookies_dict)
            result.user_profile = copy.deepcopy(self.user_profile)
            result._revoked = revoked
            return result

        target_token = str(account_token or "").strip()
        if not target_token:
            # 匿名/无账号工作区任务：不混用当前已登录的其他账号凭据
            result.cookies_dict = {}
            result.user_profile = None
            result._revoked = self._account_cancellations.setdefault("", threading.Event())
            return result

        from utils import db
        acc = db.get_zhihu_account_by_token(target_token)
        if not acc or not acc.get("is_enabled", 1):
            raise RuntimeError(f"知乎账号 {target_token} 已停用或移除，无法执行任务")
        revoked = self._account_cancellations.setdefault(target_token, threading.Event())
        if revoked.is_set():
            raise RuntimeError(f"知乎账号 {target_token} 已退出登录，无法执行任务")
        cookie_str = (acc.get("cookie") or "").strip()
        parsed_cookies = {
            k.strip(): v.strip()
            for part in cookie_str.split(";")
            if "=" in part
            for k, v in [part.strip().split("=", 1)]
            if k.strip() and v.strip() and k.strip() != "sec_token"
        }
        if not parsed_cookies.get("z_c0"):
            raise RuntimeError(f"知乎账号 {acc.get('uname') or target_token} 凭证已失效，请重新登录")

        result.cookies_dict = parsed_cookies
        result.user_profile = {
            "url_token": target_token,
            "name": acc.get("uname") or target_token,
            "avatar_url": acc.get("face", ""),
            "headline": acc.get("headline", ""),
        }
        result._revoked = revoked
        return result

    def import_cookie_string(self, raw_cookie_str: str, expected_token: str = "") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        raw = (raw_cookie_str or "").strip()
        parsed = {}
        if "=" in raw:
            for part in raw.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    if k.strip() != "sec_token":
                        parsed[k.strip()] = v.strip()
        elif raw:
            parsed["z_c0"] = raw
        if not parsed.get("z_c0"):
            return False, "请提供包含非空 z_c0 的完整 Cookie", None
        with self._lock:
            epoch = self._login_epoch
            candidate = self.snapshot()
            candidate.cookies_dict = parsed
            candidate.user_profile = None
            ok, profile = candidate.verify_login()
            if not ok or not profile or not profile.get("url_token"):
                return False, "无法确认知乎账号身份，请检查网络或重新登录", None
            with self._commit_lock:
                if epoch != self._login_epoch:
                    return False, "登录已取消", None
                if expected_token and profile["url_token"] != expected_token:
                    return False, "登录身份与所选账号不一致，请通过添加账号保存新身份", None
                from utils import db
                db.activate_zhihu_session(profile, candidate.cookies_dict)
                self._auth_session = None
                self.cookies_dict = dict(candidate.cookies_dict)
                self.user_profile = dict(profile)
                self.verification_state = "verified"
                self._verification_next = time.monotonic() + 60
                event = self._account_cancellations.get(profile["url_token"])
                if event is None or event.is_set():
                    self._account_cancellations[profile["url_token"]] = threading.Event()
                return True, f"知乎登录成功：{profile.get('name', '知乎用户')}", profile

    def _persist_invalidation(self, token):
        from utils import db
        if token:
            conn = db.get_zhihu_auth_db_connection()
            try:
                with conn:
                    conn.execute("UPDATE zhihu_accounts SET cookie = '' WHERE url_token = ?", (token,))
            finally:
                conn.close()

    def invalidate(self):
        token = (self.user_profile or {}).get("url_token", "")
        self.revoke_account_tasks(token)
        self.cookies_dict.pop("z_c0", None)
        self.user_profile = None
        self._persist_invalidation(token)
        self._save_cookies()

    def verify_login(self) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """调用 /api/v4/me 验证当前知乎登录态并拉取个人信息 (url_token, name, avatar_url)"""
        if not self.cookies_dict.get("z_c0"):
            return False, None

        dc0 = self.ensure_dc0()
        uri = "/api/v4/me?include=is_realname,url_token,name,avatar_url,headline,answer_count,articles_count,follower_count"
        sign_headers = sign_zhihu_request(uri, dc0)
        headers = {
            "User-Agent": ZHIHU_UA,
            "Accept": "application/json, text/plain, */*",
            "Cookie": self.get_cookie_header(),
            "Referer": f"{ZHIHU_URL}/",
            **sign_headers,
        }
        sess = self._new_session()
        try:
            t0 = time.time()
            full_url = f"{ZHIHU_URL}{uri}"
            resp = sess.get(full_url, headers=headers, timeout=12)
            dur = (time.time() - t0) * 1000.0
            log_http_exchange(
                platform="ZhihuAuth",
                method="GET",
                url=full_url,
                status_code=resp.status_code,
                req_headers=headers,
                resp_cookies=dict(resp.cookies),
                resp_body=resp.text[:800],
                duration_ms=dur,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("id") or data.get("url_token"):
                    self.user_profile = {
                        "id": str(data.get("id", "")),
                        "url_token": data.get("url_token", ""),
                        "name": data.get("name", "知乎用户"),
                        "avatar_url": data.get("avatar_url", ""),
                        "headline": data.get("headline", ""),
                        "answer_count": int(data.get("answer_count", 0)),
                        "articles_count": int(data.get("articles_count", 0)),
                        "follower_count": int(data.get("follower_count", 0)),
                    }
                    self.verification_state = "verified"
                    self._save_cookies()
                    return True, self.user_profile
            elif resp.status_code == 401:
                app_logger.warning("[ZhihuAuth] 知乎登录凭证已失效 (HTTP 401: %s)，请重新登录", resp.text[:200])
                self.invalidate()
                self.verification_state = "expired"
                return False, None
        except Exception as e:
            app_logger.warning("[ZhihuAuth] 校验 /api/v4/me 异常: %s", e)

        self.verification_state = "unavailable"
        return False, None

    @staticmethod
    def _encode_qr_png(qr_url: str) -> str:
        qr = qrcode.QRCode(version=1, box_size=8, border=2)
        qr.add_data(qr_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_img = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{b64_img}"

    def _get_qr_cookie_header(self) -> str:
        return "; ".join(
            f"{k}={v}" for k, v in self._qr_cookies.items() if v and k not in ("sec_token", "z_c0")
        )

    def generate_qrcode(self) -> Dict[str, Any]:
        """申请知乎扫码登录二维码：
        每次扫码强制通过全新 Session 向 /udid 申请独立的设备标识 d_c0，
        绝不复用曾从本机浏览器导入的 d_c0，防止扫码后同设备 d_c0 冲突把本机浏览器网页端顶下线。
        """
        self.cancel_login()
        epoch = self._login_epoch
        self._qr_cookies = {}
        sess = self._get_auth_session(reset=True)
        try:
            udid_resp = sess.post(
                f"{ZHIHU_URL}/udid",
                headers={
                    "User-Agent": ZHIHU_UA,
                    "Referer": f"{ZHIHU_URL}/signin",
                    "Origin": ZHIHU_URL,
                    "x-requested-with": "fetch",
                },
                timeout=8,
            )
            for k, v in udid_resp.cookies.items():
                if v and k not in ("sec_token", "z_c0"):
                    self._qr_cookies[k] = v
            if "d_c0" not in self._qr_cookies:
                rand_id = base64.b64encode(os.urandom(24)).decode("ascii").rstrip("=")
                self._qr_cookies["d_c0"] = f"{rand_id}|{int(time.time())}"

            qr_api_url = f"{ZHIHU_URL}/api/v3/account/api/login/qrcode"
            req_headers = {
                "User-Agent": ZHIHU_UA,
                "Cookie": self._get_qr_cookie_header(),
                "Referer": f"{ZHIHU_URL}/signin",
                "Origin": ZHIHU_URL,
                "Accept": "application/json, text/plain, */*",
            }
            t0 = time.time()
            resp = sess.post(qr_api_url, headers=req_headers, timeout=10)
            dur = (time.time() - t0) * 1000.0
            for k, v in resp.cookies.items():
                if v and k not in ("sec_token", "z_c0"):
                    self._qr_cookies[k] = v

            log_http_exchange(
                platform="ZhihuAuth",
                method="POST",
                url=qr_api_url,
                status_code=resp.status_code,
                req_headers=req_headers,
                resp_cookies=dict(resp.cookies),
                resp_body=resp.text,
                duration_ms=dur,
            )

            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token")
                if token and epoch == self._login_epoch:
                    self.qr_token = token
                    qr_url = data.get("link") or f"https://www.zhihu.com/account/scan/login/{token}?/api/login/qrcode"
                    qr_img = self._encode_qr_png(qr_url)
                    app_logger.info("[ZhihuAuth] 二维码生成成功: token=%s | qr_url=%s", token, qr_url)
                    return {
                        "success": True,
                        "qrcode_key": token,
                        "qrcode_url": qr_url,
                        "qrcode_img": qr_img,
                    }
            return {
                "success": False,
                "message": f"申请知乎二维码受限 (HTTP {resp.status_code})，请使用「浏览器窗口」或「Cookie 导入」",
            }
        except Exception as e:
            app_logger.error("[ZhihuAuth] 申请知乎二维码异常: %s", e)
            return {
                "success": False,
                "message": f"申请知乎二维码异常: {e}，建议使用「浏览器窗口」或「Cookie 导入」",
            }

    def poll_qrcode(self, qrcode_key: Optional[str] = None) -> Dict[str, Any]:
        """轮询知乎扫码状态（使用独立 _qr_cookies，仅在扫码成功获得非空 z_c0 时才更新正式会话）"""
        tk = qrcode_key or self.qr_token
        if not tk or tk != self.qr_token:
            return {"status": "error", "message": "未找到有效的知乎二维码会话"}

        epoch = self._login_epoch
        sess = self._get_auth_session()
        poll_path = f"/api/v3/account/api/login/qrcode/{tk}/scan_info"
        poll_url = f"{ZHIHU_URL}{poll_path}"
        dc0 = self._qr_cookies.get("d_c0") or self.ensure_dc0()
        sign_headers = sign_zhihu_request(poll_path, dc0)
        req_headers = {
            "User-Agent": ZHIHU_UA,
            "Cookie": self._get_qr_cookie_header(),
            "Referer": f"{ZHIHU_URL}/signin",
            "Accept": "application/json, text/plain, */*",
            "x-requested-with": "fetch",
            "x-xsrftoken": self._qr_cookies.get("_xsrf", ""),
            **sign_headers,
        }
        try:
            t0 = time.time()
            resp = sess.get(poll_url, headers=req_headers, timeout=14)
            dur = (time.time() - t0) * 1000.0
            for k, v in resp.cookies.items():
                if v and k != "sec_token":
                    self._qr_cookies[k] = v

            log_http_exchange(
                platform="ZhihuAuth",
                method="GET",
                url=poll_url,
                status_code=resp.status_code,
                req_headers=req_headers,
                resp_cookies=dict(resp.cookies),
                resp_body=resp.text,
                duration_ms=dur,
            )

            data = {}
            if resp.content:
                try:
                    data = resp.json()
                except Exception:
                    data = {}

            if epoch != self._login_epoch:
                return {"status": "error", "message": "登录已取消"}

            # 1. 检测知乎 40352 安全验证拦截（立即重置会话清除 sec_token 污染）
            if resp.status_code == 403:
                self.cookies_dict.pop("sec_token", None)
                self._auth_session = None
                err_obj = data.get("error") or {}
                err_code = err_obj.get("code", 403)
                redirect_url = err_obj.get("redirect", "")
                app_logger.warning(
                    "[ZhihuAuth] 扫码轮询触发知乎风控 (HTTP 403, code=%s, redirect=%s)",
                    err_code,
                    redirect_url,
                )
                return {
                    "status": "blocked",
                    "code": err_code,
                    "redirect": redirect_url,
                    "message": "当前网络触发知乎安全验证 (40352)，请点击下方「🌐 浏览器窗口扫码/过验证」一键完成登录",
                }

            # 2. 登录成功判定：响应 Cookie 含非空 z_c0 或响应 JSON 含 z_c0 / access_token / user_id
            resp_zc0 = resp.cookies.get("z_c0") or data.get("z_c0")
            if (
                resp_zc0
                or data.get("access_token")
                or data.get("user_id")
                or data.get("uid")
            ):
                candidate_cookies = dict(self._qr_cookies)
                if resp_zc0:
                    candidate_cookies["z_c0"] = str(resp_zc0)
                ok, msg, profile = self.import_cookie_string(
                    "; ".join(f"{k}={v}" for k, v in candidate_cookies.items()))
                if not ok:
                    return {"status": "error", "message": msg}
                self.qr_token = None
                uname = (profile or {}).get("name") or data.get("fullname") or "知乎用户"
                face = (profile or {}).get("avatar_url") or data.get("avatar_url") or ""
                app_logger.info("[ZhihuAuth] 扫码登录成功! 用户=%s", uname)
                return {
                    "status": "success",
                    "message": f"知乎扫码登录成功！欢迎 {uname}",
                    "uname": uname,
                    "face": face,
                    "url_token": (profile or {}).get("url_token", ""),
                }

            scan_status = data.get("status", 0)
            # 3. 关键修复：处理 status == 5 (知乎后台自动轮换二维码 new_token)，避免二维码直接消失!
            if scan_status == 5 and isinstance(data.get("new_token"), dict):
                new_token_obj = data["new_token"]
                new_tk = new_token_obj.get("token")
                if new_tk:
                    self.qr_token = new_tk
                    new_link = (
                        new_token_obj.get("link")
                        or f"https://www.zhihu.com/account/scan/login/{new_tk}?/api/login/qrcode"
                    )
                    new_img = self._encode_qr_png(new_link)
                    self._save_cookies()
                    app_logger.info("[ZhihuAuth] 二维码 Token 自动轮换: %s -> %s", tk, new_tk)
                    return {
                        "status": "rotated",
                        "qrcode_key": new_tk,
                        "qrcode_url": new_link,
                        "qrcode_img": new_img,
                        "message": "二维码已自动刷新，请使用知乎 App 扫码",
                    }

            if scan_status == 1:
                return {"status": "scanned", "message": "已扫码！请在手机知乎 App 上点击「确认登录」"}
            if scan_status == 0:
                return {"status": "waiting", "message": "请使用手机知乎 App 扫码登录"}

            return {
                "status": "expired",
                "message": data.get("message", f"二维码已过期 (status={scan_status})，请点击刷新重试"),
            }
        except Exception as e:
            app_logger.warning("[ZhihuAuth] 轮询扫码状态网络等待或异常: %s", e)
            return {"status": "waiting", "message": "等待扫码响应中..."}

    def cancel_login(self) -> None:
        with self._commit_lock:
            self._login_epoch += 1
            self._browser_cancel.set()
            self.qr_token = None
            self._qr_cookies.clear()
            self._browser_login_state = {"status": "cancelled", "message": "登录已取消"}

    def start_browser_login(self) -> Dict[str, Any]:
        if self._browser_login_state.get("status") == "running":
            return {"success": True, **self._browser_login_state}
        self.cancel_login()
        cancel = self._browser_cancel = threading.Event()
        self._browser_login_state = {"status": "running", "message": "请在浏览器中完成知乎登录"}

        def worker():
            try:
                from utils.zhihu_browser_login import run_browser_login
                captured = {}
                code = run_browser_login(timeout_sec=220, cancel_event=cancel,
                                         on_cookies=lambda cookies: captured.update(cookies))
                with self._lock:
                    if cancel.is_set():
                        return
                    ok, msg, profile = (False, "浏览器登录已关闭、超时或不可用", None)
                    if code == 0 and captured:
                        ok, msg, profile = self.import_cookie_string(
                            "; ".join(f"{k}={v}" for k, v in captured.items()))
                    if cancel.is_set():
                        return
                    self._browser_login_state = {
                        "status": "success" if ok else "failed", "message": msg,
                        "url_token": (profile or {}).get("url_token", ""),
                    }
            except Exception as exc:
                with self._lock:
                    if not cancel.is_set():
                        self._browser_login_state = {"status": "failed", "message": str(exc)}

        threading.Thread(target=worker, daemon=True).start()
        return {"success": True, **self._browser_login_state}

    def get_browser_login_status(self) -> Dict[str, Any]:
        return dict(self._browser_login_state)

    def revoke_account_tasks(self, token):
        self._account_cancellations.setdefault(token, threading.Event()).set()

    def logout(self) -> None:
        with self._lock:
            from utils import db
            db.deactivate_zhihu_session()
            self.revoke_account_tasks((self.user_profile or {}).get("url_token", ""))
            self.cancel_login()
            self.cookies_dict.clear()
            self.user_profile = None
            self._auth_session = None
            if os.path.exists(self.cookie_file):
                os.remove(self.cookie_file)


# ==================== 4. 知乎三大场景 API 客户端 (ZhihuClient) ====================

class ZhihuClient:
    """
    知乎接口与页面解析客户端：
    - 自动携带 x-zse-96 / x-zst-81 签名、Chrome 146 TLS 指纹与全局令牌桶限流器
    - 支持博主回答/文章/视频、收藏夹、个人赞同动态流、单帖/问题采集
    """

    def __init__(self, auth_manager: ZhihuAuthManager):
        self.auth = auth_manager

    def _signed_get(
        self,
        uri: str,
        params: Optional[Dict[str, Any]] = None,
        base_url: str = ZHIHU_URL,
        is_api: bool = True,
        bypass_limiter: bool = False,
    ):
        if getattr(self.auth, "_revoked", None) is not None and self.auth._revoked.is_set():
            raise RuntimeError("账号已退出、禁用或移除，归档请求已停止")
        dc0 = self.auth.ensure_dc0()
        final_uri = uri
        if params:
            qs = urllib.parse.urlencode(params, safe="[],?()*;:")
            final_uri = f"{uri}?{qs}" if "?" not in uri else f"{uri}&{qs}"

        headers = {
            "User-Agent": ZHIHU_UA,
            "Accept": (
                "application/json, text/plain, */*"
                if is_api
                else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cookie": self.auth.get_cookie_header(),
            "Referer": f"{ZHIHU_URL}/",
        }
        if is_api:
            headers.update(sign_zhihu_request(final_uri, dc0))

        full_url = f"{base_url}{final_uri}"
        if not bypass_limiter:
            zhihu_limiter.acquire(full_url)

        start_t = time.time()
        sess = cffi_requests.Session(impersonate=IMPERSONATE)
        resp = sess.get(full_url, headers=headers, timeout=18)
        duration_ms = (time.time() - start_t) * 1000.0

        for k, v in resp.cookies.items():
            if k != "sec_token":
                self.auth.cookies_dict[k] = v

        log_http_exchange(
            platform="ZhihuAPI" if is_api else "ZhihuHTML",
            method="GET",
            url=full_url,
            status_code=resp.status_code,
            req_headers=headers,
            resp_cookies=dict(resp.cookies),
            resp_body=resp.text[:800] if is_api else f"<HTML length={len(resp.text)}>",
            duration_ms=duration_ms,
        )

        if not bypass_limiter:
            is_banned = resp.status_code in (403, 429) or ("验证码" in (resp.text[:400] if is_api else ""))
            if is_banned:
                zhihu_limiter.on_banned(f"知乎风控拦截 status={resp.status_code}")
            zhihu_limiter.record_request(
                method="GET",
                url=full_url,
                status_code=resp.status_code,
                code=0 if resp.status_code == 200 else resp.status_code,
                duration_ms=duration_ms,
                is_banned=is_banned,
                retries=0,
            )
        if resp.status_code == 401:
            self.auth.invalidate()
        return resp

    # -------------------- 4.1 博主（答主）信息与内容列表 --------------------

    def get_creator_profile(self, url_token_or_link: str) -> Optional[Dict[str, Any]]:
        """获取知乎答主资料，支持传入个人主页链接或 url_token"""
        token = self.extract_url_token(url_token_or_link)
        if not token:
            return None

        uri = f"/api/v4/members/{token}"
        params = {
            "include": "allow_message,is_followed,is_following,is_org,is_blocking,employments,answer_count,follower_count,articles_count,gender,badge[?(type=best_answerer)].topics"
        }
        try:
            resp = self._signed_get(uri, params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("url_token") or data.get("name"):
                    return {
                        "url_token": data.get("url_token", token),
                        "user_id": str(data.get("id", "")),
                        "name": data.get("name", token),
                        "avatar_url": (data.get("avatar_url_template") or data.get("avatar_url") or "").replace("{size}", "xl"),
                        "headline": data.get("headline", ""),
                        "answer_count": int(data.get("answer_count", 0)),
                        "articles_count": int(data.get("articles_count", 0)),
                        "follower_count": int(data.get("follower_count", 0)),
                    }
        except Exception as e:
            print(f"[!] [ZhihuClient] API 获取答主 {token} 信息失败，尝试 HTML 兜底: {e}", flush=True)

        try:
            resp = self._signed_get(f"/people/{token}", is_api=False)
            if resp.status_code == 200:
                init_data = extract_js_initial_data(resp.text)
                users = init_data.get("initialState", {}).get("entities", {}).get("users", {})
                u = users.get(token) or (next(iter(users.values())) if users else None)
                if u:
                    return {
                        "url_token": u.get("urlToken", token),
                        "user_id": str(u.get("id", "")),
                        "name": u.get("name", token),
                        "avatar_url": u.get("avatarUrl", ""),
                        "headline": u.get("headline", ""),
                        "answer_count": int(u.get("answerCount", 0)),
                        "articles_count": int(u.get("articlesCount", 0)),
                        "follower_count": int(u.get("followerCount", 0)),
                    }
        except Exception as e:
            print(f"[!] [ZhihuClient] HTML 获取答主 {token} 信息失败: {e}", flush=True)

        return None

    def get_creator_answers(self, url_token: str, offset: int = 0, limit: int = 20) -> Tuple[List[Dict[str, Any]], bool]:
        """获取答主的回答列表（复用 MediaCrawler 参数）"""
        uri = f"/api/v4/members/{url_token}/answers"
        params = {
            "include": (
                "data[*].is_normal,admin_closed_comment,reward_info,is_collapsed,annotation_action,"
                "annotation_detail,collapse_reason,collapsed_by,suggest_edit,comment_count,can_comment,"
                "content,editable_content,attachment,voteup_count,reshipment_settings,comment_permission,"
                "created_time,updated_time,review_info,excerpt,paid_info,reaction_instruction,is_labeled,"
                "label_info,relationship.is_authorized,voting,is_author,is_thanked,is_nothelp;"
                "data[*].vessay_info;data[*].author.badge[?(type=best_answerer)].topics;"
                "data[*].author.vip_info;data[*].question.has_publishing_draft,relationship"
            ),
            "offset": offset,
            "limit": limit,
            "order_by": "created",
        }
        resp = self._signed_get(uri, params=params)
        if resp.status_code != 200:
            raise RuntimeError(f"获取答主回答失败 (HTTP {resp.status_code})")
        payload = resp.json()
        items = [
            self._normalize_content_item(raw, source_type="creator", source_id=url_token)
            for raw in payload.get("data", [])
        ]
        is_end = bool(payload.get("paging", {}).get("is_end", True))
        return [i for i in items if i], is_end

    def get_creator_articles(self, url_token: str, offset: int = 0, limit: int = 20) -> Tuple[List[Dict[str, Any]], bool]:
        """获取答主的专栏文章列表（复用 MediaCrawler 参数）"""
        uri = f"/api/v4/members/{url_token}/articles"
        params = {
            "include": (
                "data[*].comment_count,suggest_edit,is_normal,thumbnail_extra_info,thumbnail,can_comment,"
                "comment_permission,admin_closed_comment,content,excerpt,voteup_count,created,updated,"
                "upvoted_followees,voting,review_info,reaction_instruction,is_labeled,label_info;"
                "data[*].vessay_info;data[*].author.badge[?(type=best_answerer)].topics;"
                "data[*].author.vip_info;"
            ),
            "offset": offset,
            "limit": limit,
            "order_by": "created",
        }
        resp = self._signed_get(uri, params=params)
        if resp.status_code != 200:
            raise RuntimeError(f"获取答主文章失败 (HTTP {resp.status_code})")
        payload = resp.json()
        items = [
            self._normalize_content_item(raw, source_type="creator", source_id=url_token)
            for raw in payload.get("data", [])
        ]
        is_end = bool(payload.get("paging", {}).get("is_end", True))
        return [i for i in items if i], is_end

    def get_creator_videos(self, url_token: str, offset: int = 0, limit: int = 20) -> Tuple[List[Dict[str, Any]], bool]:
        """获取答主的视频列表"""
        uri = f"/api/v4/members/{url_token}/zvideos"
        params = {
            "include": "similar_zvideo,creation_relationship,reaction_instruction",
            "offset": offset,
            "limit": limit,
            "similar_aggregation": "true",
        }
        resp = self._signed_get(uri, params=params)
        if resp.status_code != 200:
            raise RuntimeError(f"获取答主视频失败 (HTTP {resp.status_code})")
        payload = resp.json()
        items = [
            self._normalize_content_item(raw, source_type="creator", source_id=url_token)
            for raw in payload.get("data", [])
        ]
        is_end = bool(payload.get("paging", {}).get("is_end", True))
        return [i for i in items if i], is_end

    def get_user_followings(self, url_token: str, offset: int = 0, limit: int = 20) -> Tuple[List[Dict[str, Any]], bool]:
        """获取指定知乎用户关注的答主列表 (/api/v4/members/{url_token}/followees)"""
        uri = f"/api/v4/members/{url_token}/followees"
        params = {
            "include": "data[*].answer_count,articles_count,gender,follower_count,is_followed,is_following,badge[?(type=best_answerer)].topics",
            "offset": offset,
            "limit": limit,
        }
        resp = self._signed_get(uri, params=params)
        if resp.status_code != 200:
            raise RuntimeError(f"获取关注答主失败 (HTTP {resp.status_code})")
        payload = resp.json()
        results: List[Dict[str, Any]] = []
        for user in payload.get("data", []):
            u_token = user.get("url_token") or ""
            if not u_token:
                continue
            results.append({
                "url_token": u_token,
                "user_id": str(user.get("id", "")),
                "name": user.get("name", u_token),
                "avatar_url": (user.get("avatar_url_template") or user.get("avatar_url") or "").replace("{size}", "xl"),
                "headline": user.get("headline", ""),
                "answer_count": int(user.get("answer_count", 0)),
                "articles_count": int(user.get("articles_count", 0)),
                "follower_count": int(user.get("follower_count", 0)),
                "group_name": "关注答主",
            })
        is_end = bool(payload.get("paging", {}).get("is_end", True))
        return results, is_end

    # -------------------- 4.2 收藏夹监听与爬取 --------------------

    def get_user_collections(self, url_token: str, include_following: bool = True) -> List[Dict[str, Any]]:
        """获取指定用户（通常为当前登录账号）创建的收藏夹与关注的收藏夹列表"""
        results: List[Dict[str, Any]] = []

        try:
            resp = self._signed_get(f"/api/v4/people/{url_token}/collections", params={"offset": 0, "limit": 50})
            if resp.status_code == 200:
                for item in resp.json().get("data", []):
                    results.append({
                        "collection_id": str(item.get("id", "")),
                        "title": item.get("title", "未命名收藏夹"),
                        "description": item.get("description", ""),
                        "creator_name": item.get("creator", {}).get("name", url_token),
                        "creator_token": item.get("creator", {}).get("url_token", url_token),
                        "item_count": int(item.get("item_count", 0)),
                        "follower_count": int(item.get("follower_count", 0)),
                        "is_public": bool(item.get("is_public", True)),
                        "source_type": "my",
                    })
        except Exception as e:
            print(f"[!] [ZhihuClient] 获取用户创建的收藏夹失败: {e}", flush=True)

        if include_following:
            try:
                resp = self._signed_get(f"/api/v4/people/{url_token}/following_collections", params={"offset": 0, "limit": 50})
                if resp.status_code == 200:
                    for item in resp.json().get("data", []):
                        results.append({
                            "collection_id": str(item.get("id", "")),
                            "title": item.get("title", "关注的收藏夹"),
                            "description": item.get("description", ""),
                            "creator_name": item.get("creator", {}).get("name", "知乎用户"),
                            "creator_token": item.get("creator", {}).get("url_token", ""),
                            "item_count": int(item.get("item_count", 0)),
                            "follower_count": int(item.get("follower_count", 0)),
                            "is_public": bool(item.get("is_public", True)),
                            "source_type": "following",
                        })
            except Exception as e:
                print(f"[!] [ZhihuClient] 获取用户关注的收藏夹失败: {e}", flush=True)

        return results

    def get_collection_detail(self, collection_id_or_url: str) -> Optional[Dict[str, Any]]:
        """获取单个收藏夹元信息"""
        cid = self.extract_collection_id(collection_id_or_url)
        if not cid:
            return None
        try:
            resp = self._signed_get(f"/api/v4/collections/{cid}")
            if resp.status_code == 200:
                data = resp.json()
                c = data.get("collection", data)
                if c.get("id") or c.get("title"):
                    return {
                        "collection_id": str(c.get("id", cid)),
                        "title": c.get("title", f"收藏夹 {cid}"),
                        "description": c.get("description", ""),
                        "creator_name": c.get("creator", {}).get("name", "知乎用户"),
                        "creator_token": c.get("creator", {}).get("url_token", ""),
                        "item_count": int(c.get("item_count", 0)),
                        "follower_count": int(c.get("follower_count", 0)),
                        "source_type": "custom",
                    }
        except Exception as e:
            print(f"[!] [ZhihuClient] 获取收藏夹 {cid} 详情异常: {e}", flush=True)
        return None

    def get_collection_items(self, collection_id: str, offset: int = 0, limit: int = 20) -> Tuple[List[Dict[str, Any]], bool]:
        """获取收藏夹内的帖子列表（支持回答、文章、视频）"""
        cid = self.extract_collection_id(collection_id)
        uri = f"/api/v4/collections/{cid}/items"
        params = {"offset": offset, "limit": limit}
        resp = self._signed_get(uri, params=params)
        if resp.status_code != 200:
            raise RuntimeError(f"获取收藏夹 {cid} 内容失败 (HTTP {resp.status_code})")

        payload = resp.json()
        items: List[Dict[str, Any]] = []
        for entry in payload.get("data", []):
            try:
                raw_content = entry.get("content") or entry
                norm = self._normalize_content_item(raw_content, source_type="collection", source_id=cid)
                if norm:
                    items.append(norm)
            except Exception as e:
                app_logger.warning("[ZhihuClient] 跳过单条异常收藏夹内容 (cid=%s): %s", cid, e)

        is_end = bool(payload.get("paging", {}).get("is_end", True))
        return items, is_end

    # -------------------- 4.3 自己点赞（赞同）的帖子监听与爬取 --------------------

    def get_member_upvoted_activities(
        self,
        url_token: str,
        after_id: Optional[str] = None,
        limit: int = 20,
    ) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
        """
        拉取指定用户（自己）的动态流 /api/v4/members/{url_token}/activities，
        筛选出点赞/赞同动作：
        - MEMBER_VOTEUP_ANSWER (赞同了回答)
        - MEMBER_VOTEUP_ARTICLE (赞同了文章)
        - MEMBER_VOTEUP_ZVIDEO (赞同了视频)
        """
        uri = f"/api/v4/members/{url_token}/activities"
        params: Dict[str, Any] = {"limit": limit, "desktop": "True"}
        if after_id:
            params["after_id"] = after_id

        resp = self._signed_get(uri, params=params)
        if resp.status_code != 200:
            raise RuntimeError(f"获取用户 {url_token} 赞同动态流失败 (HTTP {resp.status_code})")

        payload = resp.json()
        upvoted_items: List[Dict[str, Any]] = []
        upvote_verbs = {
            "MEMBER_VOTEUP_ANSWER",
            "MEMBER_VOTEUP_ARTICLE",
            "MEMBER_VOTEUP_ZVIDEO",
            "VOTE_UP_ANSWER",
            "VOTE_UP_ARTICLE",
        }

        for act in payload.get("data", []):
            verb = str(act.get("verb", "")).upper()
            action_text = str(act.get("action_text", ""))
            if verb in upvote_verbs or "赞同了" in action_text:
                target = act.get("target")
                if not isinstance(target, dict):
                    continue
                upvote_ts = int(act.get("created_time") or time.time())
                norm = self._normalize_content_item(
                    target,
                    source_type="upvote",
                    source_id=url_token,
                    upvote_time=upvote_ts,
                )
                if norm:
                    upvoted_items.append(norm)

        paging = payload.get("paging", {})
        is_end = bool(paging.get("is_end", True))
        next_url = paging.get("next", "")
        next_after_id = None
        if next_url and "after_id=" in next_url:
            m = re.search(r"after_id=([^&]+)", next_url)
            if m:
                next_after_id = m.group(1)

        return upvoted_items, next_after_id, is_end

    # -------------------- 4.4 单帖 / 问题完整正文拉取 --------------------

    def fetch_single_content_by_url(self, url: str) -> List[Dict[str, Any]]:
        url = url.strip()
        # 严格禁止知乎盐选专栏与付费市场链接
        if any(p in url.lower() for p in ("/market/paid_column", "/remix/", "/xen/market", "km.zhihu.com")):
            raise ValueError("该链接为知乎盐选/付费专栏，受知识产权与平台付费保护，系统已严格禁止解析与归档。")

        m_ans = re.search(r"/question/(\d+)/answer/(\d+)", url)
        if m_ans:
            qid, aid = m_ans.group(1), m_ans.group(2)
            item = self.get_full_answer(qid, aid)
            return [item] if item else []

        m_art = re.search(r"zhuanlan\.zhihu\.com/p/(\d+)|/p/(\d+)", url)
        if m_art:
            art_id = m_art.group(1) or m_art.group(2)
            item = self.get_full_article(art_id)
            return [item] if item else []

        m_q = re.search(r"/question/(\d+)", url)
        if m_q:
            qid = m_q.group(1)
            return self.get_question_top_answers(qid, limit=10)

        return []

    def get_full_answer(self, question_id: str, answer_id: str) -> Optional[Dict[str, Any]]:
        uri = f"/api/v4/answers/{answer_id}"
        params = {
            "include": "paid_info,label_info,content,excerpt,voteup_count,comment_count,created_time,updated_time,author,question"
        }
        try:
            resp = self._signed_get(uri, params=params)
            if resp.status_code == 200:
                return self._normalize_content_item(resp.json(), source_type="single", source_id=question_id)
        except Exception:
            pass

        try:
            resp = self._signed_get(f"/question/{question_id}/answer/{answer_id}", is_api=False)
            if resp.status_code == 200:
                init_data = extract_js_initial_data(resp.text)
                answers = init_data.get("initialState", {}).get("entities", {}).get("answers", {})
                ans = answers.get(str(answer_id)) or (next(iter(answers.values())) if answers else None)
                if ans:
                    return self._normalize_content_item(ans, source_type="single", source_id=question_id)
        except Exception as e:
            print(f"[!] [ZhihuClient] 获取单篇回答 {answer_id} 失败: {e}", flush=True)
        return None

    def get_full_article(self, article_id: str) -> Optional[Dict[str, Any]]:
        uri = f"/api/v4/articles/{article_id}"
        params = {"include": "paid_info,label_info,content,excerpt,voteup_count,comment_count,created,updated,author,title,image_url"}
        try:
            resp = self._signed_get(uri, params=params)
            if resp.status_code == 200:
                return self._normalize_content_item(resp.json(), source_type="single", source_id=article_id)
        except Exception:
            pass

        try:
            resp = self._signed_get(f"/p/{article_id}", base_url=ZHIHU_ZHUANLAN_URL, is_api=False)
            if resp.status_code == 200:
                init_data = extract_js_initial_data(resp.text)
                articles = init_data.get("initialState", {}).get("entities", {}).get("articles", {})
                art = articles.get(str(article_id)) or (next(iter(articles.values())) if articles else None)
                if art:
                    return self._normalize_content_item(art, source_type="single", source_id=article_id)
        except Exception as e:
            print(f"[!] [ZhihuClient] 获取专栏文章 {article_id} 失败: {e}", flush=True)
        return None

    def get_question_top_answers(self, question_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        uri = f"/api/v4/questions/{question_id}/answers"
        params = {
            "include": "data[*].content,excerpt,voteup_count,comment_count,created_time,updated_time,author,question",
            "offset": 0,
            "limit": min(limit, 20),
            "sort_by": "default",
        }
        try:
            resp = self._signed_get(uri, params=params)
            if resp.status_code == 200:
                return [
                    item
                    for raw in resp.json().get("data", [])
                    if (item := self._normalize_content_item(raw, source_type="single", source_id=question_id))
                ]
        except Exception as e:
            print(f"[!] [ZhihuClient] 获取问题 {question_id} 回答失败: {e}", flush=True)
        return []

    @staticmethod
    def extract_url_token(url_or_token: str) -> str:
        s = (url_or_token or "").strip().rstrip("/")
        if not s:
            return ""
        m = re.search(r"zhihu\.com/(?:people|org)/([^/?#]+)", s)
        if m:
            return m.group(1)
        return s.split("/")[-1].split("?")[0]

    @staticmethod
    def extract_collection_id(url_or_id: str) -> str:
        s = (url_or_id or "").strip().rstrip("/")
        if not s:
            return ""
        m = re.search(r"collection/(\d+)", s)
        if m:
            return m.group(1)
        return re.sub(r"\D", "", s) or s

    def _normalize_content_item(
        self,
        raw: Dict[str, Any],
        source_type: str = "creator",
        source_id: str = "",
        upvote_time: int = 0,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None

        raw_type = str(raw.get("type", "answer")).lower()
        content_id = str(raw.get("id", ""))
        if not content_id:
            return None

        author = raw.get("author") or {}
        author_name = author.get("name", "知乎用户")
        author_token = author.get("url_token") or author.get("urlToken") or ""
        author_avatar = author.get("avatar_url") or author.get("avatarUrl") or ""

        question = raw.get("question") or {}
        question_id = str(question.get("id", "")) if question else ""

        if raw_type == "article":
            content_type = "article"
            title = raw.get("title") or "未命名专栏文章"
            content_url = f"{ZHIHU_ZHUANLAN_URL}/p/{content_id}"
            created_time = int(raw.get("created") or raw.get("created_time") or raw.get("createdTime") or 0)
            updated_time = int(raw.get("updated") or raw.get("updated_time") or raw.get("updatedTime") or created_time)
        elif raw_type == "zvideo":
            content_type = "zvideo"
            title = raw.get("title") or raw.get("description") or "知乎视频"
            content_url = f"{ZHIHU_URL}/zvideo/{content_id}"
            created_time = int(raw.get("published_at") or raw.get("created_at") or raw.get("created_time") or 0)
            updated_time = int(raw.get("updated_at") or created_time)
        else:
            content_type = "answer"
            title = question.get("title") or raw.get("title") or f"知乎回答 #{content_id}"
            content_url = (
                f"{ZHIHU_URL}/question/{question_id}/answer/{content_id}"
                if question_id
                else f"{ZHIHU_URL}/answer/{content_id}"
            )
            created_time = int(raw.get("created_time") or raw.get("createdTime") or 0)
            updated_time = int(raw.get("updated_time") or raw.get("updatedTime") or created_time)

        raw_html = _coerce_html_str(raw.get("content") or raw.get("description") or "")
        # 清理 noscript 并提升知乎懒加载 data-actualsrc/data-original 真实图片链接至 src
        if raw_html:
            clean_html = re.sub(r"<noscript>.*?</noscript>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
            def _fix_img_src(m):
                tag = m.group(0)
                real_m = (
                    re.search(r'data-actualsrc=["\']([^"\']+)["\']', tag, re.IGNORECASE)
                    or re.search(r'data-original=["\']([^"\']+)["\']', tag, re.IGNORECASE)
                )
                if real_m:
                    real_url = real_m.group(1).strip()
                    if real_url.startswith("//"): real_url = "https:" + real_url
                    if not real_url.startswith("data:"):
                        tag = re.sub(r'src=["\'][^"\']*["\']', f'src="{real_url}"', tag, count=1, flags=re.IGNORECASE)
                return tag
            content_html = re.sub(r"<img\b[^>]*>", _fix_img_src, clean_html, flags=re.IGNORECASE)
        else:
            content_html = ""

        raw_excerpt = raw.get("excerpt") or raw.get("excerpt_title") or ""
        excerpt = strip_html_tags(raw_excerpt) if not isinstance(raw_excerpt, str) else raw_excerpt
        if not excerpt and content_html:
            plain = strip_html_tags(content_html)
            excerpt = plain[:180] + ("..." if len(plain) > 180 else "")
        if not isinstance(title, str) or not title.strip():
            title = (excerpt[:40] if excerpt else f"知乎内容 #{content_id}")

        cover_url = (
            raw.get("thumbnail")
            or raw.get("image_url")
            or raw.get("title_image")
            or (raw.get("thumbnail_extra_info") or {}).get("url", "")
        )

        # Only explicit content-level payment metadata is conclusive. Summary
        # truncation, relationship authorization and prose keywords are not paywalls.
        paid_info = raw.get("paid_info") or {}
        paid_info = paid_info if isinstance(paid_info, dict) else {}
        paid_flag = paid_info.get("is_paid", raw.get("is_paid"))
        is_paid = paid_flag is True or paid_flag == 1 or paid_flag == "true"
        paid_type = paid_info.get("paid_type", paid_info.get("type"))
        known_paid_types = ("paid", "paid_column", "salt", "yanxuan")
        if isinstance(paid_type, str) and paid_type.lower() in known_paid_types:
            is_paid = True
        def label_values(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in ("text", "name", "title") and isinstance(child, str):
                        yield child.strip()
                    elif isinstance(child, (dict, list)):
                        yield from label_values(child)
            elif isinstance(value, list):
                for child in value:
                    yield from label_values(child)
            elif isinstance(value, str):
                yield value.strip()
        if any(label in ("盐选专栏", "付费内容", "会员专属")
               for label in label_values(raw.get("label_info"))):
            is_paid = True
        unknown_type = paid_type not in (None, "", 0, "0", "free", "normal") and not is_paid
        paid_reason = "明确付费内容标记" if is_paid else ""
        paid_check_version = 0 if unknown_type else 2

        return {
            "content_id": content_id,
            "content_type": content_type,
            "question_id": question_id,
            "title": title,
            "excerpt": excerpt,
            "content_html": content_html,
            "content_url": content_url,
            "cover_url": cover_url or "",
            "author_name": author_name,
            "author_token": author_token,
            "author_avatar": author_avatar,
            "voteup_count": int(raw.get("voteup_count") or raw.get("voteupCount") or 0),
            "comment_count": int(raw.get("comment_count") or raw.get("commentCount") or 0),
            "created_time": created_time,
            "updated_time": updated_time,
            "source_type": source_type,
            "source_id": source_id or author_token,
            "upvote_time": upvote_time,
            "is_paid": is_paid,
            "paid_reason": paid_reason,
            "paid_check_version": paid_check_version,
            "is_restricted": 1 if is_paid else 0,
            "restrict_reason": paid_reason,
        }


# ==================== 5. 知乎富文本离线归档器 (ZhihuArchiver) ====================

class ZhihuArchiver:
    """
    负责将知乎回答 / 专栏文章持久化到本地磁盘：
    {download_dir}/zhihu/{分类目录}/{标题_content_id}/
      ├─ content.md   (带 YAML Front-Matter 与本地图片相对路径)
      ├─ content.html (带独立阅读器样式的离线 HTML)
      ├─ images/      (正文全部高清配图)
      └─ info.json    (完整结构化元数据)
    """

    def __init__(self, client: Optional[ZhihuClient] = None):
        self.client = client

    def archive_item(
        self,
        item: Dict[str, Any],
        base_download_dir: str,
        category_folder: Optional[str] = None,
        progress_cb: Optional[Callable[[str, int], None]] = None,
    ) -> str:
        content_id = str(item.get("content_id", ""))
        content_type = item.get("content_type", "answer")
        question_id = item.get("question_id", "")

        content_html = item.get("content_html") or ""
        if len(content_html) < 40 and self.client:
            if progress_cb:
                progress_cb("正在拉取完整富文本正文...", 10)
            full_item = None
            if content_type == "answer" and question_id:
                full_item = self.client.get_full_answer(question_id, content_id)
            elif content_type == "article":
                full_item = self.client.get_full_article(content_id)
            if full_item and (full_item.get("is_paid") or full_item.get("paid_check_version", 0) < 2):
                raise ValueError("详情为付费内容或付费状态尚未确认，不能归档")
            if full_item and full_item.get("content_html"):
                content_html = full_item["content_html"]
                item["content_html"] = content_html

        if not category_folder:
            source_type = item.get("source_type", "creator")
            if source_type == "upvote":
                category_folder = "我赞同的帖子"
            elif source_type == "collection":
                category_folder = f"收藏夹_{item.get('source_id', '默认')}"
            else:
                category_folder = item.get("author_name") or item.get("author_token") or "未分类答主"

        norm_base = os.path.normpath(base_download_dir)
        zhihu_root = norm_base if os.path.basename(norm_base).lower() == "zhihu" else os.path.join(norm_base, "zhihu")
        safe_cat = sanitize_filename(category_folder) or "zhihu_archive"
        safe_title = sanitize_filename(item.get("title", f"zhihu_{content_id}"))[:60]
        item_dir = os.path.join(zhihu_root, safe_cat, f"{safe_title}_{content_id}")
        images_dir = os.path.join(item_dir, "images")
        os.makedirs(images_dir, exist_ok=True)

        localized_html, md_body, image_count = self._localize_html_and_convert_md(
            content_html or f"<p>{html_lib.escape(item.get('excerpt', ''))}</p>",
            images_dir,
            progress_cb=progress_cb,
        )

        created_str = datetime.fromtimestamp(item.get("created_time") or time.time()).strftime("%Y-%m-%d %H:%M:%S")
        archived_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        front_matter = [
            "---",
            f'title: "{item.get("title", "").replace(chr(34), chr(39))}"',
            f'content_id: "{content_id}"',
            f'content_type: "{content_type}"',
            f'author: "{item.get("author_name", "")}"',
            f'author_token: "{item.get("author_token", "")}"',
            f'url: "{item.get("content_url", "")}"',
            f"voteup_count: {item.get('voteup_count', 0)}",
            f"comment_count: {item.get('comment_count', 0)}",
            f'created_at: "{created_str}"',
            f'archived_at: "{archived_str}"',
            f"image_count: {image_count}",
            "---",
            "",
            f"# {item.get('title', '')}",
            "",
            f"> **作者**：{item.get('author_name', '')} (`{item.get('author_token', '')}`)  ",
            f"> **原帖链接**：[{item.get('content_url', '')}]({item.get('content_url', '')})  ",
            f"> **数据统计**：👍 {item.get('voteup_count', 0)} 赞同 · 💬 {item.get('comment_count', 0)} 评论 · 发布于 {created_str}",
            "",
            "---",
            "",
            md_body,
            "",
        ]
        md_path = os.path.join(item_dir, "content.md")
        tmp_md_path = os.path.join(item_dir, f".content.md.{os.getpid()}.{threading.get_ident()}.part")
        with open(tmp_md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(front_matter))
        os.replace(tmp_md_path, md_path)

        html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>{html_lib.escape(item.get("title", ""))}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #e2e8f0; max-width: 820px; margin: 0 auto; padding: 32px 20px; line-height: 1.75; }}
    h1 {{ color: #f8fafc; font-size: 24px; margin-bottom: 12px; }}
    .meta {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 12px 16px; font-size: 13px; color: #94a3b8; margin-bottom: 24px; }}
    .meta a {{ color: #60a5fa; text-decoration: none; }}
    article {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 24px; }}
    img {{ max-width: 100%; height: auto; border-radius: 8px; margin: 12px 0; display: block; }}
    blockquote {{ border-left: 4px solid #3b82f6; margin: 16px 0; padding: 8px 16px; background: #0f172a; color: #cbd5e1; border-radius: 0 8px 8px 0; }}
    pre, code {{ background: #0f172a; color: #f1f5f9; border-radius: 6px; padding: 2px 6px; font-family: Consolas, monospace; overflow-x: auto; }}
    pre {{ padding: 14px; }}
  </style>
</head>
<body>
  <h1>{html_lib.escape(item.get("title", ""))}</h1>
  <div class="meta">
    <div><strong>作者：</strong>{html_lib.escape(item.get("author_name", ""))} ({html_lib.escape(item.get("author_token", ""))})</div>
    <div><strong>统计：</strong>👍 {item.get("voteup_count", 0)} 赞同 · 💬 {item.get("comment_count", 0)} 评论 · 发布于 {created_str}</div>
    <div><strong>原链接：</strong><a href="{html_lib.escape(item.get("content_url", ""))}" target="_blank">{html_lib.escape(item.get("content_url", ""))}</a></div>
  </div>
  <article>{localized_html}</article>
</body>
</html>"""
        html_path = os.path.join(item_dir, "content.html")
        tmp_html_path = os.path.join(item_dir, f".content.html.{os.getpid()}.{threading.get_ident()}.part")
        with open(tmp_html_path, "w", encoding="utf-8") as f:
            f.write(html_doc)
        os.replace(tmp_html_path, html_path)

        info_payload = dict(item)
        info_payload["archived_at"] = archived_str
        info_payload["image_count"] = image_count
        info_payload["local_dir"] = item_dir
        info_path = os.path.join(item_dir, "info.json")
        tmp_info_path = os.path.join(item_dir, f".info.json.{os.getpid()}.{threading.get_ident()}.part")
        with open(tmp_info_path, "w", encoding="utf-8") as f:
            json.dump(info_payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_info_path, info_path)

        if progress_cb:
            progress_cb("归档完成", 100)
        return md_path

    def _localize_html_and_convert_md(
        self,
        raw_html: str,
        images_dir: str,
        progress_cb: Optional[Callable[[str, int], None]] = None,
    ) -> Tuple[str, str, int]:
        # 1. 移除冗余的 noscript 块
        clean_html = re.sub(r"<noscript>.*?</noscript>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)

        # 2. 扫描所有 img 标签并本地化下载
        img_pattern = re.compile(r"<img\b[^>]*>", flags=re.IGNORECASE)
        img_matches = list(img_pattern.finditer(clean_html))
        total_imgs = len(img_matches)
        downloaded_count = 0
        sess = cffi_requests.Session(impersonate=IMPERSONATE)

        url_map: Dict[str, str] = {}
        for idx, m in enumerate(img_matches, start=1):
            tag_str = m.group(0)
            src_m = (
                re.search(r'data-original=["\']([^"\']+)["\']', tag_str, re.IGNORECASE)
                or re.search(r'data-actualsrc=["\']([^"\']+)["\']', tag_str, re.IGNORECASE)
                or re.search(r'src=["\']([^"\']+)["\']', tag_str, re.IGNORECASE)
            )
            if not src_m:
                continue
            src = src_m.group(1).strip()
            if not src or src.startswith("data:"):
                continue
            if src.startswith("//"):
                src = "https:" + src

            ext = ".jpg"
            m_ext = re.search(r"\.(jpg|jpeg|png|gif|webp)", src, re.IGNORECASE)
            if m_ext:
                ext = "." + m_ext.group(1).lower()
            img_filename = f"img_{idx:03d}{ext}"
            local_img_path = os.path.join(images_dir, img_filename)

            try:
                if not os.path.exists(local_img_path):
                    resp = sess.get(
                        src,
                        headers={"User-Agent": ZHIHU_UA, "Referer": f"{ZHIHU_URL}/"},
                        timeout=12,
                    )
                    if resp.status_code == 200 and resp.content:
                        with open(local_img_path, "wb") as img_f:
                            img_f.write(resp.content)
                        downloaded_count += 1
                else:
                    downloaded_count += 1
                url_map[tag_str] = f'<img src="./images/{img_filename}" alt="配图_{idx}" />'
            except Exception as e:
                print(f"[!] [ZhihuArchiver] 下载配图失败 ({src}): {e}", flush=True)

            if progress_cb and total_imgs > 0:
                pct = 20 + int((idx / total_imgs) * 65)
                progress_cb(f"正在下载配图 ({idx}/{total_imgs})...", pct)

        localized_html = clean_html
        for old_tag, new_tag in url_map.items():
            localized_html = localized_html.replace(old_tag, new_tag)

        md_text = self._html_to_markdown(localized_html)
        return localized_html, md_text, downloaded_count

    @staticmethod
    def _html_to_markdown(html_str: str) -> str:
        """零外部依赖的轻量 HTML -> Markdown 转换器"""
        s = html_str
        s = re.sub(r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>', r"\n\n![配图](\1)\n\n", s, flags=re.IGNORECASE)
        s = re.sub(r"<h1[^>]*>(.*?)</h1>", r"\n# \1\n", s, flags=re.DOTALL | re.IGNORECASE)
        s = re.sub(r"<h2[^>]*>(.*?)</h2>", r"\n## \1\n", s, flags=re.DOTALL | re.IGNORECASE)
        s = re.sub(r"<h3[^>]*>(.*?)</h3>", r"\n### \1\n", s, flags=re.DOTALL | re.IGNORECASE)
        s = re.sub(r"<b[^>]*>(.*?)</b>|<strong[^>]*>(.*?)</strong>", lambda m: f"**{m.group(1) or m.group(2)}**", s, flags=re.DOTALL | re.IGNORECASE)
        s = re.sub(r"<blockquote[^>]*>(.*?)</blockquote>", lambda m: "\n> " + strip_html_tags(m.group(1)) + "\n", s, flags=re.DOTALL | re.IGNORECASE)
        s = re.sub(r"<pre[^>]*><code[^>]*>(.*?)</code></pre>", lambda m: f"\n```\n{html_lib.unescape(m.group(1))}\n```\n", s, flags=re.DOTALL | re.IGNORECASE)
        s = re.sub(r"<li[^>]*>(.*?)</li>", lambda m: f"- {strip_html_tags(m.group(1))}\n", s, flags=re.DOTALL | re.IGNORECASE)
        s = re.sub(r"</p>|<br\s*/?>", "\n\n", s, flags=re.IGNORECASE)
        s = re.sub(r"<[^>]+>", "", s)
        s = html_lib.unescape(s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()


# 全局单例
zhihu_auth = ZhihuAuthManager()
zhihu_client = ZhihuClient(zhihu_auth)
zhihu_archiver = ZhihuArchiver(zhihu_client)
