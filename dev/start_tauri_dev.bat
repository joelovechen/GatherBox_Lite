@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
echo ========================================================
echo   GatherBox (知乎 + B站) 开发者热重载调试模式
echo   本地持久化日志: %~dp0..\logs\app.log
echo ========================================================
cd /d "%~dp0bili-app"
pnpm tauri dev
pause
