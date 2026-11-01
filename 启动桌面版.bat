@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

title GatherBox 桌面客户端
echo ========================================================
echo   GatherBox - 桌面客户端启动中 (Apple HIG + M3 设计规范)
echo ========================================================
echo.
if not exist "%~dp0dev\bili-app\src-tauri\target\release\GatherBoxLite.exe" (
    echo [!] 未找到桌面程序，请先运行 构建桌面版.bat。
    pause
    exit /b 1
)
if not exist "%~dp0dev\.venv\Scripts\python.exe" (
    echo [!] 未找到 Python 虚拟环境，请按 README.md 安装后端依赖。
    pause
    exit /b 1
)
echo [*] 正在启动客户端...
start "" "%~dp0dev\bili-app\src-tauri\target\release\GatherBoxLite.exe"
if errorlevel 1 (
    echo [!] 客户端启动失败。
    pause
    exit /b 1
)
echo [*] 已发送客户端启动请求。
echo.
exit /b
