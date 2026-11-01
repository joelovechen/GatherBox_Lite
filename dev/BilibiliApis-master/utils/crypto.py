# -*- coding: utf-8 -*-
"""本地敏感凭据（Cookie / RefreshToken / Session）加密与解密模块.

特性：
1. 零第三方依赖（纯 Python 标准库 ctypes + hashlib + hmac + base64），完美兼容 Nuitka 独立打包。
2. Windows 平台优先使用系统级 DPAPI (CryptProtectData / CryptUnprotectData)，密钥由 Windows
   操作系统内核绑定当前登录用户与机器凭据，数据库文件即使被拷贝到其他机器也无法解密。
3. 跨平台或极端环境下提供基于机器特征 + PBKDF2-HMAC-SHA256 + CTR 流加密 + HMAC-SHA256 认证加密兜底。
4. 幂等接口设计：带前缀标识 ("DPAPIv1:" / "ENCv1:")，避免重复加密，并支持旧版明文数据平滑迁移。
"""

import base64
import ctypes
from ctypes import wintypes
import getpass
import hashlib
import hmac
import os
import platform
import struct
import sys
import uuid
from typing import Optional

DPAPI_PREFIX = "DPAPIv1:"
FALLBACK_PREFIX = "ENCv1:"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def is_encrypted(val: Optional[str]) -> bool:
    """判断字符串是否已处于加密格式."""
    if not val or not isinstance(val, str):
        return False
    return val.startswith(DPAPI_PREFIX) or val.startswith(FALLBACK_PREFIX)


def _dpapi_encrypt(raw_bytes: bytes) -> Optional[bytes]:
    if sys.platform != "win32":
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        buf = ctypes.create_string_buffer(raw_bytes, len(raw_bytes))
        blob_in = _DATA_BLOB(len(raw_bytes), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DATA_BLOB()

        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
        if not ok:
            return None
        try:
            encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            return encrypted
        finally:
            if blob_out.pbData:
                kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def _dpapi_decrypt(enc_bytes: bytes) -> Optional[bytes]:
    if sys.platform != "win32":
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        buf = ctypes.create_string_buffer(enc_bytes, len(enc_bytes))
        blob_in = _DATA_BLOB(len(enc_bytes), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DATA_BLOB()

        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
        if not ok:
            return None
        try:
            decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            return decrypted
        finally:
            if blob_out.pbData:
                kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def _derive_fallback_keys() -> tuple[bytes, bytes]:
    """基于本机硬件节点与用户特征派生 256-bit 加密密钥与 256-bit HMAC 密钥."""
    try:
        user = getpass.getuser()
    except Exception:
        user = "gatherbox_user"
    machine_id = f"{platform.node()}|{uuid.getnode()}|{user}|GatherBox-Credential-Vault-v1"
    salt = b"GatherBox_Lite_SQLite_Cookie_Salt_2026"
    dk = hashlib.pbkdf2_hmac("sha256", machine_id.encode("utf-8"), salt, 60000, dklen=64)
    return dk[:32], dk[32:]


def _keystream_xor(data: bytes, enc_key: bytes, nonce: bytes) -> bytes:
    out = bytearray(len(data))
    block_count = (len(data) + 31) // 32
    for counter in range(block_count):
        block = hmac.new(
            enc_key,
            nonce + struct.pack(">Q", counter),
            hashlib.sha256,
        ).digest()
        start = counter * 32
        end = min(start + 32, len(data))
        for i in range(start, end):
            out[i] = data[i] ^ block[i - start]
    return bytes(out)


def _fallback_encrypt(raw_bytes: bytes) -> bytes:
    enc_key, mac_key = _derive_fallback_keys()
    nonce = os.urandom(16)
    ciphertext = _keystream_xor(raw_bytes, enc_key, nonce)
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    return nonce + tag + ciphertext


def _fallback_decrypt(blob: bytes) -> Optional[bytes]:
    if len(blob) < 48:
        return None
    enc_key, mac_key = _derive_fallback_keys()
    nonce = blob[:16]
    tag = blob[16:48]
    ciphertext = blob[48:]
    expected_tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        return None
    return _keystream_xor(ciphertext, enc_key, nonce)


def encrypt_secret(plaintext: Optional[str]) -> str:
    """将敏感明文字符串（如 Cookie、refresh_token、会话 JSON）加密为密文字符串."""
    if not plaintext:
        return ""
    if not isinstance(plaintext, str):
        plaintext = str(plaintext)
    if is_encrypted(plaintext):
        return plaintext

    raw_bytes = plaintext.encode("utf-8")
    dpapi_blob = _dpapi_encrypt(raw_bytes)
    if dpapi_blob is not None:
        return DPAPI_PREFIX + base64.b64encode(dpapi_blob).decode("ascii")

    fb_blob = _fallback_encrypt(raw_bytes)
    return FALLBACK_PREFIX + base64.b64encode(fb_blob).decode("ascii")


def decrypt_secret(value: Optional[str]) -> str:
    """将数据库中的密文还原为明文；若传入的是未加密的旧数据，则原样返回以保障兼容."""
    if not value:
        return ""
    if not isinstance(value, str):
        value = str(value)

    if value.startswith(DPAPI_PREFIX):
        b64_part = value[len(DPAPI_PREFIX):]
        try:
            enc_bytes = base64.b64decode(b64_part)
            plain_bytes = _dpapi_decrypt(enc_bytes)
            if plain_bytes is not None:
                return plain_bytes.decode("utf-8")
        except Exception:
            pass
        return ""

    if value.startswith(FALLBACK_PREFIX):
        b64_part = value[len(FALLBACK_PREFIX):]
        try:
            enc_bytes = base64.b64decode(b64_part)
            plain_bytes = _fallback_decrypt(enc_bytes)
            if plain_bytes is not None:
                return plain_bytes.decode("utf-8")
        except Exception:
            pass
        return ""

    # 兼容尚未迁移的旧明文
    return value
