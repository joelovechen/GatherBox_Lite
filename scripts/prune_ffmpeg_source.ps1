<#
.SYNOPSIS
    基于 MSVC 编译产物 (.o) 与递归 #include 依赖图，从完整 FFmpeg 源码树自动提取最小自包含源码树 (tools/ffmpeg-minimal-src)。
#>
param(
    [string]$FullSourceDir = "",
    [string]$BuildObjectsDir = "",
    [string]$OutputMinimalSrcDir = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $FullSourceDir) { $FullSourceDir = Join-Path $ProjectRoot "release\FFmpeg-n8.1.3" }
if (-not $OutputMinimalSrcDir) { $OutputMinimalSrcDir = Join-Path $ProjectRoot "tools\ffmpeg-minimal-src" }

if (-not (Test-Path (Join-Path $FullSourceDir "configure"))) {
    throw "未找到完整 FFmpeg 源码目录: $FullSourceDir"
}
if (-not $BuildObjectsDir -or -not (Test-Path $BuildObjectsDir)) {
    throw "请提供已完成编译的构建目录 (-BuildObjectsDir) 以提取 .o 依赖图。"
}

$keepRelPaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

# 1. 顶层构建与许可证文件
foreach ($f in @("Makefile", "configure", "COPYING.LGPLv2.1", "COPYING.LGPLv3", "LICENSE.md", "README.md", "RELEASE")) {
    if (Test-Path (Join-Path $FullSourceDir $f)) { [void]$keepRelPaths.Add($f) }
}

# 2. ffbuild/ 与 compat/ 全部支撑脚本及兼容垫片
foreach ($dir in @("ffbuild", "compat")) {
    Get-ChildItem (Join-Path $FullSourceDir $dir) -Recurse -File | ForEach-Object {
        [void]$keepRelPaths.Add($_.FullName.Substring($FullSourceDir.Length + 1))
    }
}

# 3. 各子模块 Makefile、版本头文件与 configure 组件注册表源文件
Get-ChildItem $FullSourceDir -Recurse -Include "Makefile","version.h","version_major.h" -File | ForEach-Object {
    $rel = $_.FullName.Substring($FullSourceDir.Length + 1)
    if ($rel -notmatch '^(tests|doc|tools)\\') {
        [void]$keepRelPaths.Add($rel)
    }
}
foreach ($reg in @(
    "libavfilter\allfilters.c",
    "libavdevice\alldevices.c",
    "libavformat\allformats.c",
    "libavformat\protocols.c",
    "libavcodec\allcodecs.c",
    "libavcodec\parsers.c",
    "libavcodec\bitstream_filters.c",
    "libavcodec\hwaccels.h",
    "fftools\resources\graph.css",
    "fftools\resources\graph.html"
)) {
    if (Test-Path (Join-Path $FullSourceDir $reg)) { [void]$keepRelPaths.Add($reg) }
}

# 4. 收集所有实际参与编译的 .o 对应的 .c 源文件
Get-ChildItem $BuildObjectsDir -Recurse -Filter "*.o" -File | ForEach-Object {
    $relO = $_.FullName.Substring($BuildObjectsDir.Length + 1)
    $relC = [System.IO.Path]::ChangeExtension($relO, ".c")
    if (Test-Path (Join-Path $FullSourceDir $relC)) {
        [void]$keepRelPaths.Add($relC)
    }
}

# 5. 递归扫描所有 #include "..." 依赖的头文件 (.h) 与模板代码 (.c / .inc)
$queue = [System.Collections.Generic.Queue[string]]::new()
foreach ($p in $keepRelPaths) {
    if ($p -match '\.(c|h|inc)$') { $queue.Enqueue($p) }
}
while ($queue.Count -gt 0) {
    $currRel = $queue.Dequeue()
    $currFull = Join-Path $FullSourceDir $currRel
    if (-not (Test-Path $currFull)) { continue }
    $currDir = Split-Path -Parent $currFull
    $topLibDir = Join-Path $FullSourceDir ($currRel.Split('\')[0])
    foreach ($line in [System.IO.File]::ReadLines($currFull)) {
        if ($line -match '^\s*#\s*include\s*"([^"]+)"') {
            $inc = $Matches[1].Replace('/', '\')
            $cand1 = [System.IO.Path]::GetFullPath((Join-Path $currDir $inc))
            $cand2 = [System.IO.Path]::GetFullPath((Join-Path $FullSourceDir $inc))
            $cand3 = [System.IO.Path]::GetFullPath((Join-Path $topLibDir $inc))
            foreach ($cand in @($cand1, $cand2, $cand3)) {
                if ($cand.StartsWith($FullSourceDir, [System.StringComparison]::OrdinalIgnoreCase) -and (Test-Path $cand -PathType Leaf)) {
                    $relCand = $cand.Substring($FullSourceDir.Length + 1)
                    if ($keepRelPaths.Add($relCand)) {
                        $queue.Enqueue($relCand)
                    }
                }
            }
        }
    }
}

# 6. 复制裁剪后的源码树到目标目录
if (Test-Path $OutputMinimalSrcDir) { Remove-Item -Recurse -Force $OutputMinimalSrcDir }
New-Item -ItemType Directory -Force -Path $OutputMinimalSrcDir | Out-Null

foreach ($rel in $keepRelPaths) {
    $srcFile = Join-Path $FullSourceDir $rel
    $dstFile = Join-Path $OutputMinimalSrcDir $rel
    $dstDir = Split-Path -Parent $dstFile
    if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
    Copy-Item -Path $srcFile -Destination $dstFile -Force
}

$totalBytes = (Get-ChildItem $OutputMinimalSrcDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host "[OK] 裁剪完成: 共保留 $($keepRelPaths.Count) 个文件，总大小 $([math]::Round($totalBytes / 1MB, 2)) MB -> $OutputMinimalSrcDir" -ForegroundColor Green
