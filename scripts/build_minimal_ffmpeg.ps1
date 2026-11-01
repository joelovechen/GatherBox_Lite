<#
.SYNOPSIS
    从裁剪后的 FFmpeg 8.1.3 源码树 (tools/ffmpeg-minimal-src) 本地编译极简纯静态 ffmpeg.exe。
.DESCRIPTION
    专为 GatherBox Lite 的 B 站 DASH 音视频无损混流 (-c copy -f mp4) 场景定制：
    - 使用 VS 2022 MSVC (--toolchain=msvc) + Git Bash + GNU Make 编译
    - 静态链接 VC 运行时 (-MT)，仅依赖 Windows 系统自带 DLL (KERNEL32/SHELL32/PSAPI/bcrypt)
    - 默认构建体积 ~2.20 MB（含 aac/ac3/eac3/flac 帧头探测解码器），启用 -UltraSlim 时约 ~1.77 MB（相比原版 80 MB 缩减 >97%）
#>
param(
    [switch]$UltraSlim = $false,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$MinimalSrcDir = Join-Path $ProjectRoot "tools\ffmpeg-minimal-src"
$BuildDir = Join-Path $ProjectRoot "release\ffmpeg_minimal_build"
if (-not $OutputPath) {
    $OutputPath = Join-Path $ProjectRoot "dev\BilibiliApis-master\ffmpeg.exe"
}

if (-not (Test-Path (Join-Path $MinimalSrcDir "configure"))) {
    throw "未找到裁剪后的 FFmpeg 源码目录: $MinimalSrcDir"
}

# 1. 检测本地编译工具链 (MSVC vcvars64.bat, Git Bash, GNU Make)
$VcVarsCandidates = @(
    "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat"
)
$VcVarsBat = $VcVarsCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $VcVarsBat) {
    throw "未找到 VS 2022 vcvars64.bat，请确认已安装 Visual Studio 2022 C++ 生成工具。"
}

$GitBash = "C:\Program Files\Git\bin\bash.exe"
if (-not (Test-Path $GitBash)) {
    throw "未找到 Git Bash: $GitBash"
}

$MakeCmd = Get-Command make -ErrorAction SilentlyContinue
$MakeDir = ""
if ($MakeCmd) {
    $MakeDir = Split-Path -Parent $MakeCmd.Source
} else {
    $NdkRoot = Join-Path $env:LOCALAPPDATA "Android\Sdk\ndk"
    if (Test-Path $NdkRoot) {
        $FoundMake = Get-ChildItem -Path $NdkRoot -Recurse -Filter "make.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($FoundMake) { $MakeDir = $FoundMake.DirectoryName }
    }
}
if (-not $MakeDir) {
    throw "未找到 GNU make.exe，请确保 PATH 或 Android NDK 中包含 make.exe。"
}

$MakeDirPosix = "/" + ($MakeDir.Substring(0, 1).ToLower()) + ($MakeDir.Substring(2).Replace("\", "/"))
$DecoderOpt = if ($UltraSlim) { "" } else { "--enable-decoder=aac,ac3,eac3,flac" }
$ModeLabel = if ($UltraSlim) { "UltraSlim (Zero-Decoder, ~1.77 MB)" } else { "Standard Minimal (Audio Header Probing, ~2.20 MB)" }

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " GatherBox Lite - Minimal FFmpeg 8.1.3 Local Builder" -ForegroundColor Cyan
Write-Host " Mode: $ModeLabel" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

if (Test-Path $BuildDir) {
    Remove-Item -Recurse -Force $BuildDir
}
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

# 2. 执行 ./configure
Write-Host "`n[Step 1/2] Running FFmpeg ./configure (--toolchain=msvc)..." -ForegroundColor Yellow
$CfgBatPath = Join-Path $BuildDir "run_configure.bat"
$CfgBatContent = @"
@echo off
call "$VcVarsBat" >nul
set "MSYS2_ARG_CONV_EXCL=*"
set "VSLANG=1033"
cd /d "$BuildDir"
"$GitBash" -c "export PATH=\"`$PATH:$MakeDirPosix\"; ../../tools/ffmpeg-minimal-src/configure --toolchain=msvc --arch=x86_64 --enable-static --disable-shared --enable-small --disable-debug --disable-doc --disable-htmlpages --disable-manpages --disable-podpages --disable-txtpages --disable-ffplay --disable-ffprobe --enable-ffmpeg --disable-avdevice --disable-swresample --disable-swscale --enable-avcodec --enable-avformat --enable-avfilter --disable-network --disable-autodetect --disable-x86asm --disable-asm --disable-everything --enable-protocol=file,pipe --enable-demuxer=mov,flac,aac,ac3,eac3,matroska --enable-muxer=mp4,ipod --enable-parser=h264,hevc,av1,vp9,aac,ac3,flac --enable-bsf=aac_adtstoasc,h264_mp4toannexb,hevc_mp4toannexb,av1_frame_merge,extract_extradata,null $DecoderOpt --extra-cflags=\"-MT -O1 -Oi -Gy -Gw -GS-\" --extra-ldflags=\"-OPT:REF -OPT:ICF\""
"@
Set-Content -Path $CfgBatPath -Value $CfgBatContent -Encoding ASCII
& cmd.exe /c $CfgBatPath
if ($LASTEXITCODE -ne 0) {
    throw "FFmpeg configure failed (ExitCode=$LASTEXITCODE)"
}

# 将 MSYS 绝对源码路径转换为相对路径以兼容原生 Win32 make.exe
$SrcPosixPattern = "/" + ($MinimalSrcDir.Substring(0, 1).ToLower()) + ($MinimalSrcDir.Substring(2).Replace("\", "/"))
foreach ($f in @("$BuildDir\Makefile", "$BuildDir\ffbuild\config.mak")) {
    $content = [System.IO.File]::ReadAllText($f)
    $content = $content.Replace($SrcPosixPattern, "../../tools/ffmpeg-minimal-src")
    [System.IO.File]::WriteAllText($f, $content)
}

# 3. 执行 make 并行编译
Write-Host "`n[Step 2/2] Compiling minimal ffmpeg.exe with MSVC & GNU Make..." -ForegroundColor Yellow
$MakeBatPath = Join-Path $BuildDir "run_make.bat"
$MakeBatContent = @"
@echo off
call "$VcVarsBat" >nul
set "MSYS2_ARG_CONV_EXCL=*"
set "VSLANG=1033"
cd /d "$BuildDir"
"$GitBash" -c "export PATH=\"`$PATH:$MakeDirPosix\"; make -j16 SHELL=sh.exe"
"@
Set-Content -Path $MakeBatPath -Value $MakeBatContent -Encoding ASCII
& cmd.exe /c $MakeBatPath
if ($LASTEXITCODE -ne 0) {
    throw "FFmpeg compilation failed (ExitCode=$LASTEXITCODE)"
}

$CompiledExe = Join-Path $BuildDir "ffmpeg.exe"
if (-not (Test-Path $CompiledExe)) {
    throw "编译未生成 ffmpeg.exe: $CompiledExe"
}

$OutDir = Split-Path -Parent $OutputPath
if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
}
Copy-Item -Path $CompiledExe -Destination $OutputPath -Force
Remove-Item -Recurse -Force $BuildDir -ErrorAction SilentlyContinue

$FinalSizeMB = [math]::Round((Get-Item $OutputPath).Length / 1MB, 2)
Write-Host "`n[OK] Minimal FFmpeg compiled successfully: $OutputPath ($FinalSizeMB MB)" -ForegroundColor Green
