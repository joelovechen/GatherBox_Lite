$ErrorActionPreference = 'Stop'

# Only close the executable built by this checkout, not other GatherBox copies.
$desktopExe = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'bili-app/src-tauri/target/release/GatherBoxLite.exe'))
$legacyExe = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'bili-app/src-tauri/target/release/bili-app.exe'))
try {
    $desktopProcesses = @(Get-Process -Name 'GatherBoxLite', 'bili-app' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $desktopExe -or $_.Path -eq $legacyExe })
    foreach ($desktopProcess in $desktopProcesses) {
        Write-Host "[desktop:build] Closing this project's desktop app (PID $($desktopProcess.Id))..."
        if ($desktopProcess.MainWindowHandle -eq 0) {
            Write-Host '[desktop:build] Stopping a background instance with no main window...'
            Stop-Process -InputObject $desktopProcess -ErrorAction Stop
        } elseif (-not $desktopProcess.CloseMainWindow()) {
            throw 'Cannot request window close. Close GatherBox manually and rebuild.'
        }
    }
    foreach ($desktopProcess in $desktopProcesses) {
        if (-not $desktopProcess.WaitForExit(15000)) {
            throw 'GatherBox is still running. Close its windows and rebuild.'
        }
    }
    if (Test-Path -LiteralPath $desktopExe) {
        # Catch remaining locks before spending time compiling the frontend.
        $desktopFile = [IO.File]::Open($desktopExe, 'Open', 'ReadWrite', 'None')
        $desktopFile.Dispose()
    }
} catch {
    Write-Host "[desktop:build] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
exit 0
