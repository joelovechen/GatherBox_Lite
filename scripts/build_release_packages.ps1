param(
    [switch]$SkipNuitka = $false,
    [switch]$SkipTauri = $false
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VersionJsonPath = Join-Path $ProjectRoot "version.json"
$VersionInfo = Get-Content $VersionJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
$AppVersion = $VersionInfo.version

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " GatherBox Lite v$AppVersion Dual-Package Build (Nuitka + WebView2)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$PythonExe = Join-Path $ProjectRoot "dev\.venv\Scripts\python.exe"
$BackendSrcDir = Join-Path $ProjectRoot "dev\BilibiliApis-master"
$FrontendDir = Join-Path $ProjectRoot "dev\bili-app"
$ReleaseRoot = Join-Path $ProjectRoot "release"
$NuitkaBuildDir = Join-Path $ReleaseRoot "nuitka_build"
$NuitkaDistDir = Join-Path $NuitkaBuildDir "server.dist"
$StagingPortable = Join-Path $ReleaseRoot "staging_portable\GatherBox_Lite"
$StagingSetup = Join-Path $ReleaseRoot "staging_setup"
$DistDir = Join-Path $ReleaseRoot "dist"

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

# Ensure minimal standalone ffmpeg.exe (~2.20 MB) is present in BackendSrcDir
$MinimalFfmpegExe = Join-Path $BackendSrcDir "ffmpeg.exe"
if (-not (Test-Path $MinimalFfmpegExe)) {
    Write-Host "`n[Step 0/4] Minimal ffmpeg.exe not found, building from tools/ffmpeg-minimal-src..." -ForegroundColor Yellow
    & (Join-Path $ProjectRoot "scripts\build_minimal_ffmpeg.ps1") -OutputPath $MinimalFfmpegExe
}

# 1. Nuitka standalone compilation for Python backend
if (-not $SkipNuitka -or -not (Test-Path (Join-Path $NuitkaDistDir "bili_backend.exe"))) {
    Write-Host "`n[Step 1/4] Compiling Python backend with Nuitka --standalone..." -ForegroundColor Yellow
    Push-Location $BackendSrcDir
    try {
        & $PythonExe -m nuitka `
            --standalone `
            --msvc=latest `
            --assume-yes-for-downloads `
            --include-package=apis,builder,utils,uvicorn,fastapi,starlette `
            --include-module=downloader,download_manager,zhihu_core,zhihu_router `
            --include-package-data=certifi `
            --nofollow-import-to=tkinter,unittest,pydoc,test,distutils,lib2to3,turtle,idlelib,doctest,imageio_ffmpeg `
            --windows-icon-from-ico="$(Join-Path $FrontendDir 'src-tauri\icons\icon.ico')" `
            --output-dir="$NuitkaBuildDir" `
            --output-filename=bili_backend.exe `
            server.py
        if ($LASTEXITCODE -ne 0) { throw "Nuitka compilation failed (ExitCode=$LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "`n[Step 1/4] Reusing compiled Nuitka backend: $NuitkaDistDir" -ForegroundColor Green
}

# 2. Build Vue 3 frontend + Tauri 2 Rust host (GatherBoxLite.exe)
$TauriExePath = Join-Path $FrontendDir "src-tauri\target\release\GatherBoxLite.exe"
if (-not $SkipTauri -or -not (Test-Path $TauriExePath)) {
    Write-Host "`n[Step 2/4] Building Vue 3 frontend & Tauri 2 host binary (GatherBoxLite.exe)..." -ForegroundColor Yellow
    Get-Process -Name "GatherBoxLite", "bili-app", "bili_backend" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Push-Location $FrontendDir
    try {
        pnpm desktop:build
        if ($LASTEXITCODE -ne 0) { throw "Tauri build failed (ExitCode=$LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "`n[Step 2/4] Reusing compiled Tauri host binary: $TauriExePath" -ForegroundColor Green
}

# 3. Assemble Package 1: Portable & Hot-Update Zip (without WebView2 bootstrapper)
Write-Host "`n[Step 3/4] Building Package 1 (Portable & Hot-Update Zip, no WebView2 installer)..." -ForegroundColor Yellow
if (Test-Path (Split-Path -Parent $StagingPortable)) {
    Remove-Item -Path (Split-Path -Parent $StagingPortable) -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StagingPortable | Out-Null

Copy-Item -Path $TauriExePath -Destination (Join-Path $StagingPortable "GatherBoxLite.exe") -Force
Copy-Item -Path $VersionJsonPath -Destination (Join-Path $StagingPortable "version.json") -Force
Copy-Item -Path (Join-Path $ProjectRoot "scripts\update_apply.ps1") -Destination (Join-Path $StagingPortable "update_apply.ps1") -Force
Copy-Item -Path $NuitkaDistDir -Destination (Join-Path $StagingPortable "bili_backend") -Recurse -Force

# Prune unused Pillow codecs, watchfiles, and 80MB imageio_ffmpeg; bundle 2.20MB minimal ffmpeg.exe
$BackendStaging = Join-Path $StagingPortable "bili_backend"
Remove-Item -Path "$BackendStaging\PIL\_avif.pyd", "$BackendStaging\PIL\_imagingft.pyd", "$BackendStaging\PIL\_webp.pyd", "$BackendStaging\PIL\_imagingcms.pyd" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "$BackendStaging\watchfiles", "$BackendStaging\imageio_ffmpeg" -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -Path $MinimalFfmpegExe -Destination (Join-Path $BackendStaging "ffmpeg.exe") -Force

# Ship the retained FFmpeg license texts and a source location with both packages.
$FfmpegLicenseDir = Join-Path $StagingPortable "licenses\ffmpeg"
New-Item -ItemType Directory -Force -Path $FfmpegLicenseDir | Out-Null
$FfmpegSourceDir = Join-Path $ProjectRoot "tools\ffmpeg-minimal-src"
foreach ($LicenseFile in @("LICENSE.md", "COPYING.LGPLv2.1", "COPYING.LGPLv3", "COPYING.GPLv2", "COPYING.GPLv3")) {
    Copy-Item -LiteralPath (Join-Path $FfmpegSourceDir $LicenseFile) -Destination $FfmpegLicenseDir -Force
}
$SourceRef = "main"
if (Get-Command git -ErrorAction SilentlyContinue) {
    $GitRef = git -C $ProjectRoot rev-parse HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $GitRef -match '^[0-9a-f]{40}$') { $SourceRef = $GitRef }
}
@"
FFmpeg is distributed under its own licenses, reproduced in this directory.
Trimmed source: https://github.com/joelovechen/GatherBox_Lite/tree/$SourceRef/tools/ffmpeg-minimal-src
Build instructions: https://github.com/joelovechen/GatherBox_Lite/blob/$SourceRef/docs/ffmpeg.md
Build script: scripts/build_minimal_ffmpeg.ps1
"@ | Set-Content -LiteralPath (Join-Path $FfmpegLicenseDir "SOURCE.txt") -Encoding UTF8

$PortableZipPath = Join-Path $DistDir "GatherBox_Lite_v${AppVersion}_win64_portable.zip"
if (Test-Path $PortableZipPath) { Remove-Item $PortableZipPath -Force }
Compress-Archive -Path $StagingPortable -DestinationPath $PortableZipPath -CompressionLevel Optimal -Force
$PortableSizeMB = [math]::Round((Get-Item $PortableZipPath).Length / 1MB, 2)
Write-Host "  -> Package 1 ready: $PortableZipPath ($PortableSizeMB MB)" -ForegroundColor Green

# 4. Assemble & Compile Package 2: NSIS Guided Installer (with Nuitka binary + WebView2 bootstrapper)
Write-Host "`n[Step 4/4] Building Package 2 (NSIS Guided Setup with built-in WebView2 installer)..." -ForegroundColor Yellow
if (Test-Path $StagingSetup) {
    Remove-Item -Path $StagingSetup -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StagingSetup | Out-Null
Copy-Item -Path (Join-Path $StagingPortable "*") -Destination $StagingSetup -Recurse -Force

$SetupResDir = Join-Path $StagingSetup "resources"
New-Item -ItemType Directory -Force -Path $SetupResDir | Out-Null
$WebView2Bootstrapper = Join-Path $FrontendDir "src-tauri\resources\MicrosoftEdgeWebview2Setup.exe"
Copy-Item -Path $WebView2Bootstrapper -Destination (Join-Path $SetupResDir "MicrosoftEdgeWebview2Setup.exe") -Force

$NsiPath = Join-Path $ProjectRoot "scripts\installer.nsi"
$NsiContent = [System.IO.File]::ReadAllText($NsiPath, [System.Text.Encoding]::UTF8)
$Utf8Bom = New-Object System.Text.UTF8Encoding($true)
[System.IO.File]::WriteAllText($NsiPath, $NsiContent, $Utf8Bom)

$MakensisCmd = Get-Command makensis -ErrorAction SilentlyContinue
$MakensisExe = if ($MakensisCmd) { $MakensisCmd.Source } else { Join-Path $env:LOCALAPPDATA "tauri\NSIS\makensis.exe" }
if (-not (Test-Path $MakensisExe)) {
    throw "makensis.exe not found. Please install NSIS or Tauri NSIS toolchain."
}

$SetupExePath = Join-Path $DistDir "GatherBox_Lite_v${AppVersion}_x64-setup.exe"
if (Test-Path $SetupExePath) { Remove-Item $SetupExePath -Force }

& $MakensisExe `
    "/DAPP_VERSION=$AppVersion" `
    "/DSTAGING_DIR=$StagingSetup" `
    "/DOUT_FILE=$SetupExePath" `
    "/DICON_FILE=$(Join-Path $FrontendDir 'src-tauri\icons\icon.ico')" `
    $NsiPath
if ($LASTEXITCODE -ne 0) { throw "NSIS installer compilation failed (ExitCode=$LASTEXITCODE)" }

$SetupSizeMB = [math]::Round((Get-Item $SetupExePath).Length / 1MB, 2)
Write-Host "  -> Package 2 ready: $SetupExePath ($SetupSizeMB MB)" -ForegroundColor Green

Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host " Dual-Package Build Completed Successfully!" -ForegroundColor Green
Write-Host " [Package 1 - Portable / Hot-Update] $PortableZipPath ($PortableSizeMB MB)"
Write-Host " [Package 2 - Guided Setup + WV2]    $SetupExePath ($SetupSizeMB MB)"
Write-Host "==========================================================" -ForegroundColor Cyan
