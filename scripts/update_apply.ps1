param(
    [Parameter(Mandatory=$true)]
    [string]$ZipPath,
    [Parameter(Mandatory=$false)]
    [string]$AppDir = $PSScriptRoot,
    [Parameter(Mandatory=$false)]
    [int]$WaitPid = 0
)

$ErrorActionPreference = "Stop"

Write-Host "[Updater] Preparing to apply update from: $ZipPath"
if (-not (Test-Path $ZipPath)) {
    Write-Error "Update zip not found: $ZipPath"
    exit 1
}

# 1. 等待旧主进程与后端进程退出
if ($WaitPid -gt 0) {
    for ($i = 0; $i -lt 30; $i++) {
        $p = Get-Process -Id $WaitPid -ErrorAction SilentlyContinue
        if (-not $p) { break }
        Start-Sleep -Milliseconds 500
    }
}

Get-Process -Name "GatherBoxLite", "bili-app", "bili_backend" -ErrorAction SilentlyContinue | ForEach-Object {
    try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch {}
}
Start-Sleep -Seconds 1

# 2. 解压便携更新包到临时目录
$StagingDir = Join-Path $env:TEMP ("gatherbox_update_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $StagingDir | Out-Null

try {
    Expand-Archive -Path $ZipPath -DestinationPath $StagingDir -Force
    $SourceRoot = $StagingDir
    if ((Test-Path (Join-Path $StagingDir "GatherBox_Lite\GatherBoxLite.exe")) -or (Test-Path (Join-Path $StagingDir "GatherBox_Lite\bili-app.exe"))) {
        $SourceRoot = Join-Path $StagingDir "GatherBox_Lite"
    }

    # 3. 覆盖主程序与版本清单（严格保护 download/ 用户数据目录，并清理旧版遗留的 bili-app.exe）
    $legacyExe = Join-Path $AppDir "bili-app.exe"
    if ((Test-Path (Join-Path $SourceRoot "GatherBoxLite.exe")) -and (Test-Path $legacyExe)) {
        Remove-Item -Path $legacyExe -Force -ErrorAction SilentlyContinue
    }
    foreach ($file in @("GatherBoxLite.exe", "bili-app.exe", "version.json", "update_apply.ps1")) {
        $srcFile = Join-Path $SourceRoot $file
        if (Test-Path $srcFile) {
            Copy-Item -Path $srcFile -Destination (Join-Path $AppDir $file) -Force
        }
    }

    # 4. 覆盖 Nuitka 后端目录 bili_backend/
    $srcBackend = Join-Path $SourceRoot "bili_backend"
    $dstBackend = Join-Path $AppDir "bili_backend"
    if (Test-Path $srcBackend) {
        if (Test-Path $dstBackend) {
            Remove-Item -Path $dstBackend -Recurse -Force
        }
        Copy-Item -Path $srcBackend -Destination $dstBackend -Recurse -Force
    }

    Write-Host "[Updater] Update applied successfully. Relaunching GatherBoxLite..."
    $exePath = Join-Path $AppDir "GatherBoxLite.exe"
    if (-not (Test-Path $exePath)) {
        $exePath = Join-Path $AppDir "bili-app.exe"
    }
    if (Test-Path $exePath) {
        Start-Process -FilePath $exePath -WorkingDirectory $AppDir
    }
} finally {
    if (Test-Path $StagingDir) {
        Remove-Item -Path $StagingDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
