# -*- coding: utf-8 -*-
"""
知乎真实浏览器窗口辅助登录脚本 (dev/BilibiliApis-master/utils/zhihu_browser_login.py)
零第三方依赖（无需安装 Playwright）：
1. 自动探测本机已安装的 Chrome 或 Edge 浏览器 (chrome.exe / msedge.exe)
2. 以独立 App 窗口 + Chrome DevTools Protocol (--remote-debugging-port) 打开 https://www.zhihu.com/signin
3. 通过原生 WebSocket + CDP (Network.getAllCookies) 实时读取包括 HttpOnly 在内的完整 Cookie
4. 一旦检测到 z_c0 登录凭证，立即加密写入 SQLite 数据库 (encrypted_sessions) 并自动关闭浏览器窗口，绝不落盘明文 Cookie 文件
"""

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


def find_browser_executable() -> Optional[str]:
    """探测 Windows 本机已安装的 Chrome 或 Edge 可执行文件路径"""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        shutil.which("chrome"),
        shutil.which("msedge"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ws_connect(ws_url: str, timeout: float = 5.0) -> socket.socket:
    """使用纯 Python 标准库建立本地 CDP WebSocket 连接"""
    without_scheme = ws_url.replace("ws://", "").replace("wss://", "")
    parts = without_scheme.split("/", 1)
    host_port = parts[0].split(":")
    host = host_port[0]
    port = int(host_port[1])
    path = "/" + (parts[1] if len(parts) > 1 else "")

    sock = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(handshake.encode("ascii"))
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("WebSocket handshake closed unexpectedly")
        resp += chunk
    return sock


def _ws_send_json(sock: socket.socket, payload_dict: Dict[str, Any]) -> None:
    payload = json.dumps(payload_dict).encode("utf-8")
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack(">H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack(">Q", length))
    mask = os.urandom(4)
    header.extend(mask)
    masked = bytearray(length)
    for i in range(length):
        masked[i] = payload[i] ^ mask[i % 4]
    sock.sendall(header + masked)


def _ws_recv_text(sock: socket.socket) -> str:
    def recv_exact(n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise EOFError("WebSocket EOF")
            buf += chunk
        return buf

    while True:
        b1, b2 = recv_exact(2)
        opcode = b1 & 0x0F
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", recv_exact(8))[0]
        if b2 & 0x80:
            mask = recv_exact(4)
            raw = bytearray(recv_exact(length))
            for i in range(length):
                raw[i] ^= mask[i % 4]
            data = bytes(raw)
        else:
            data = recv_exact(length)

        if opcode == 0x8:  # Close frame
            raise EOFError("WebSocket closed by peer")
        if opcode == 0x9:  # Ping -> send Pong
            pong = bytearray([0x8A, 0x80]) + os.urandom(4)
            sock.sendall(pong)
            continue
        if opcode in (0x1, 0x2):
            return data.decode("utf-8", errors="replace")


def _cdp_get_all_cookies(ws_url: str) -> List[Dict[str, Any]]:
    sock = _ws_connect(ws_url, timeout=4.0)
    try:
        msg_id = 1001
        _ws_send_json(sock, {"id": msg_id, "method": "Network.getAllCookies", "params": {}})
        deadline = time.time() + 4.0
        while time.time() < deadline:
            raw = _ws_recv_text(sock)
            msg = json.loads(raw)
            if msg.get("id") == msg_id:
                return (msg.get("result") or {}).get("cookies") or []
        return []
    finally:
        try:
            sock.close()
        except Exception:
            pass


def run_browser_login(cookie_file: str = "", timeout_sec: int = 220, cancel_event=None, on_cookies=None) -> int:
    print("[*] Starting Zhihu browser login (target: encrypted SQLite storage)", flush=True)
    browser_exe = find_browser_executable()
    if not browser_exe:
        print("未找到本机已安装的 Chrome 或 Edge 浏览器", file=sys.stderr, flush=True)
        return 2

    port = pick_free_port()
    tmp_profile = tempfile.mkdtemp(prefix="gatherbox_zhihu_login_")
    print(f"[*] Launching browser: {browser_exe} (CDP port={port})", flush=True)

    cmd = [
        browser_exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={tmp_profile}",
        "--remote-allow-origins=*",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1080,760",
        "--app=https://www.zhihu.com/signin",
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        shutil.rmtree(tmp_profile, ignore_errors=True)
        raise

    try:
        start_ts = time.time()
        cdp_ready = False
        empty_tabs_count = 0

        while time.time() - start_ts < timeout_sec:
            if cancel_event is not None and cancel_event.is_set():
                return 1
            if proc.poll() is not None:
                print("[!] Browser process exited by user", file=sys.stderr, flush=True)
                break

            try:
                req = urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2.0)
                tabs = json.loads(req.read().decode("utf-8", errors="replace"))
                cdp_ready = True
            except Exception:
                if cdp_ready and proc.poll() is not None:
                    break
                time.sleep(0.8)
                continue

            page_tabs = [
                t for t in tabs
                if isinstance(t, dict)
                and t.get("type") == "page"
                and t.get("webSocketDebuggerUrl")
            ]
            if not page_tabs:
                empty_tabs_count += 1
                if cdp_ready and empty_tabs_count >= 3:
                    print("[!] Browser window closed by user", file=sys.stderr, flush=True)
                    break
                time.sleep(0.8)
                continue

            empty_tabs_count = 0
            ws_url = page_tabs[0]["webSocketDebuggerUrl"]

            try:
                cookies = _cdp_get_all_cookies(ws_url)
                zhihu_cookies: Dict[str, str] = {}
                for c in cookies:
                    domain = str(c.get("domain") or "")
                    name = str(c.get("name") or "")
                    value = str(c.get("value") or "")
                    if (domain.lstrip(".") == "zhihu.com" or domain.endswith(".zhihu.com")) and name and value and name != "sec_token":
                        zhihu_cookies[name] = value

                if zhihu_cookies.get("z_c0"):
                    if cancel_event is not None and cancel_event.is_set():
                        return 1
                    if on_cookies is None:
                        # CLI uses the same verified commit path as the UI.
                        from zhihu_core import zhihu_auth
                        ok, _, _ = zhihu_auth.import_cookie_string(
                            "; ".join(f"{k}={v}" for k, v in zhihu_cookies.items()))
                        if not ok:
                            return 1
                    else:
                        on_cookies(zhihu_cookies)
                    return 0
            except Exception:
                pass

            time.sleep(1.0)

        return 1
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
        except Exception:
            pass
        shutil.rmtree(tmp_profile, ignore_errors=True)


def main() -> int:
    cookie_file = sys.argv[1] if len(sys.argv) >= 2 else ""
    return run_browser_login(cookie_file)


if __name__ == "__main__":
    sys.exit(main())
