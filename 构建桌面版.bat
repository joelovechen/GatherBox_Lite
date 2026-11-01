@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0dev\bili-app"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev\prepare_desktop_build.ps1"
if errorlevel 1 (
    echo [!] 桌面程序仍被占用，请关闭 GatherBox 后重新构建。
    pause
    exit /b 1
)
echo [*] 正在构建 GatherBox 桌面版（包含前端资源，无需 Vite 服务）...
call pnpm desktop:build
if errorlevel 1 (
    echo [!] 构建失败，请查看上方错误。
    pause
    exit /b 1
)
echo [*] 构建完成，可运行根目录的 启动桌面版.bat。
pause
exit /b 0
