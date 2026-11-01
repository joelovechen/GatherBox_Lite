@echo off
chcp 65001 >nul
echo [GatherBox Lite] 正在启动 Nuitka + WebView2 双包发行构建流程...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_release_packages.ps1" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] 构建过程中发生错误，请检查上方输出日志。
    pause
    exit /b %ERRORLEVEL%
)
echo.
pause
