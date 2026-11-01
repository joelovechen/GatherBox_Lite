"""
统一日志模块：同时输出到控制台 (stdout) 与本地旋转日志文件 (logs/app.log)。
支持对知乎、B站以及本地 FastAPI 的请求参数、响应状态码、响应头与响应体内容进行详细打印与持久化（自动脱敏敏感 Cookie/Token）。
"""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, Optional

from utils.paths import get_app_root

# 默认日志目录：应用根目录下的 logs/app.log
PROJECT_ROOT = get_app_root()
LOG_DIR = PROJECT_ROOT / "logs"
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    LOG_DIR = Path.cwd() / "logs"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE_PATH = LOG_DIR / "app.log"

_SENSITIVE_JSON_RE = re.compile(
    r'("(?:z_c0|access_token|refresh_token|SESSDATA|bili_jct|cookie)"\s*:\s*")([^"]+)(")',
    re.IGNORECASE,
)


def setup_logger(name: str = "GatherBox") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. 控制台 Handler (实时打印)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    # 2. 本地文件 Handler (10MB 轮转，保留 5 个备份，UTF-8 编码)
    try:
        file_handler = RotatingFileHandler(
            str(LOG_FILE_PATH),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"[!] 初始化日志文件失败 ({LOG_FILE_PATH}): {e}", flush=True)

    return logger


app_logger = setup_logger("GatherBox")


def _truncate_text(val: Any, max_len: int = 1200) -> str:
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        try:
            text = json.dumps(val, ensure_ascii=False)
        except Exception:
            text = str(val)
    else:
        text = str(val)
    text = text.replace("\r", " ").replace("\n", " ")
    text = _SENSITIVE_JSON_RE.sub(r"\1***REDACTED***\3", text)
    if len(text) > max_len:
        return text[:max_len] + f"... [truncated, total {len(text)} chars]"
    return text


def log_http_exchange(
    platform: str,
    method: str,
    url: str,
    status_code: int,
    req_headers: Optional[Dict[str, Any]] = None,
    resp_cookies: Optional[Dict[str, Any]] = None,
    resp_body: Any = None,
    duration_ms: float = 0.0,
    extra: str = "",
) -> None:
    """记录外部平台 (知乎/B站) HTTP 请求与响应详情到控制台及本地文件."""
    body_preview = _truncate_text(resp_body, max_len=1000)
    cookie_keys = list(resp_cookies.keys()) if resp_cookies else []
    sig_info = ""
    if req_headers:
        zse96 = req_headers.get("x-zse-96") or req_headers.get("X-Zse-96")
        if zse96:
            sig_info = f" | x-zse-96={zse96[:28]}..."

    msg = (
        f"[{platform}] {method.upper()} {url} -> HTTP {status_code} ({duration_ms:.1f}ms)"
        f"{sig_info}"
        f"{f' | Set-Cookie={cookie_keys}' if cookie_keys else ''}"
        f"{f' | {extra}' if extra else ''}"
        f" | Response: {body_preview}"
    )
    if status_code >= 400:
        app_logger.warning(msg)
    else:
        app_logger.info(msg)
    try:
        sys.stdout.flush()
    except Exception:
        pass
