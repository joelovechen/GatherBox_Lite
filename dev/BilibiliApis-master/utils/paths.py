"""Portable source-mode & Nuitka-compiled data directory; BILI_DATA_DIR overrides the default."""

import os
import sys
from pathlib import Path


def get_app_root() -> Path:
    """Resolve the application root directory in both Nuitka/frozen mode and source mode."""
    is_compiled = "__compiled__" in globals() or getattr(sys, "frozen", False)
    exe_name = Path(sys.argv[0]).name.lower() if sys.argv else ""
    if is_compiled or exe_name.startswith("bili_backend"):
        exe_dir = Path(sys.argv[0]).resolve().parent
        if exe_dir.name.lower() in ("bili_backend", "bili_backend.dist", "binaries"):
            return exe_dir.parent
        return exe_dir
    return Path(__file__).resolve().parents[3]


def default_data_dir() -> str:
    env_dir = os.environ.get("BILI_DATA_DIR")
    if env_dir:
        return env_dir
    app_root = get_app_root()
    # macOS 打包为 .app 时，避免将数据存储在 .app 包内部导致覆盖更新时丢失数据库
    if sys.platform == "darwin" and ".app/Contents" in str(app_root):
        mac_data_dir = Path.home() / "Library" / "Application Support" / "GatherBox_Lite" / "download"
        mac_data_dir.mkdir(parents=True, exist_ok=True)
        return str(mac_data_dir)
    return str(app_root / "download")
