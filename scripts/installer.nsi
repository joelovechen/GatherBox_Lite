Unicode True
SetCompressor /SOLID lzma
SetCompressorDictSize 64

!include "MUI2.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "0.2.0"
!endif
!ifndef STAGING_DIR
  !define STAGING_DIR "..\release\staging_setup"
!endif
!ifndef OUT_FILE
  !define OUT_FILE "..\release\dist\GatherBox_Lite_v${APP_VERSION}_x64-setup.exe"
!endif
!ifndef ICON_FILE
  !define ICON_FILE "..\dev\bili-app\src-tauri\icons\icon.ico"
!endif

Name "GatherBox Lite v${APP_VERSION}"
OutFile "${OUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\GatherBox_Lite"
InstallDirRegKey HKCU "Software\GatherBox_Lite" "Install_Dir"
RequestExecutionLevel user
BrandingText "GatherBox Lite v${APP_VERSION} (Nuitka Native + WebView2)"

!define MUI_ICON "${ICON_FILE}"
!define MUI_UNICON "${ICON_FILE}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\GatherBoxLite.exe"
!define MUI_FINISHPAGE_RUN_TEXT "立即运行 GatherBox Lite"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

Section "GatherBox Lite 主程序 (必选)" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"

  ; 结束正在运行的实例以释放文件锁（兼容旧版 bili-app.exe）
  nsExec::ExecToLog 'taskkill /F /IM GatherBoxLite.exe /T'
  nsExec::ExecToLog 'taskkill /F /IM bili-app.exe /T'
  nsExec::ExecToLog 'taskkill /F /IM bili_backend.exe /T'
  Delete "$INSTDIR\bili-app.exe"

  ; 释放主程序、Nuitka 原生后端与内置 WebView2 引导安装器
  File /r "${STAGING_DIR}\*.*"

  ; 检测系统是否已安装 Microsoft Edge WebView2 Runtime
  ; 注册表 GUID: {F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}
  SetRegView 64
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 WebView2Present
  StrCmp $0 "0.0.0.0" 0 WebView2Present

  ReadRegStr $0 HKLM "SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 WebView2Present
  StrCmp $0 "0.0.0.0" 0 WebView2Present

  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 WebView2Present
  StrCmp $0 "0.0.0.0" 0 WebView2Present

  ; 未检测到 WebView2，调用内置 MicrosoftEdgeWebview2Setup.exe 在线安装
  DetailPrint "未检测到 WebView2 Runtime，正在启动内置 WebView2 在线引导安装程序..."
  IfFileExists "$INSTDIR\resources\MicrosoftEdgeWebview2Setup.exe" 0 WebView2Done
    ExecWait '"$INSTDIR\resources\MicrosoftEdgeWebview2Setup.exe" /silent /install' $1
    IntCmp $1 0 WebView2Done
    DetailPrint "静默安装返回码 $1，切换为常规交互安装..."
    ExecWait '"$INSTDIR\resources\MicrosoftEdgeWebview2Setup.exe" /install'
    Goto WebView2Done

  WebView2Present:
    DetailPrint "检测到已安装 Microsoft Edge WebView2 Runtime (版本: $0)，跳过安装。"

  WebView2Done:
  ; 写入安装路径与卸载信息
  WriteRegStr HKCU "Software\GatherBox_Lite" "Install_Dir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GatherBox_Lite" "DisplayName" "GatherBox Lite"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GatherBox_Lite" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GatherBox_Lite" "Publisher" "GatherBox"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GatherBox_Lite" "DisplayIcon" "$INSTDIR\GatherBoxLite.exe,0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GatherBox_Lite" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GatherBox_Lite" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GatherBox_Lite" "NoRepair" 1
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; 创建开始菜单与桌面快捷方式（先删除旧快捷方式再重建，强制刷新图标缓存）
  Delete "$DESKTOP\GatherBox Lite.lnk"
  CreateDirectory "$SMPROGRAMS\GatherBox Lite"
  CreateShortCut "$SMPROGRAMS\GatherBox Lite\GatherBox Lite.lnk" "$INSTDIR\GatherBoxLite.exe" "" "$INSTDIR\GatherBoxLite.exe" 0
  CreateShortCut "$SMPROGRAMS\GatherBox Lite\卸载 GatherBox Lite.lnk" "$INSTDIR\Uninstall.exe" "" "$INSTDIR\Uninstall.exe" 0
  CreateShortCut "$DESKTOP\GatherBox Lite.lnk" "$INSTDIR\GatherBoxLite.exe" "" "$INSTDIR\GatherBoxLite.exe" 0
  System::Call 'shell32.dll::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
SectionEnd

Section "Uninstall"
  nsExec::ExecToLog 'taskkill /F /IM GatherBoxLite.exe /T'
  nsExec::ExecToLog 'taskkill /F /IM bili-app.exe /T'
  nsExec::ExecToLog 'taskkill /F /IM bili_backend.exe /T'

  Delete "$INSTDIR\GatherBoxLite.exe"
  Delete "$INSTDIR\bili-app.exe"
  Delete "$INSTDIR\version.json"
  Delete "$INSTDIR\update_apply.ps1"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR\bili_backend"
  RMDir /r "$INSTDIR\resources"
  ; 注意：默认保留 $INSTDIR\download 用户数据目录，防止误删已下载媒体与数据库
  RMDir "$INSTDIR"

  Delete "$DESKTOP\GatherBox Lite.lnk"
  RMDir /r "$SMPROGRAMS\GatherBox Lite"

  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GatherBox_Lite"
  DeleteRegKey HKCU "Software\GatherBox_Lite"
SectionEnd
