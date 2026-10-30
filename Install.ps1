# Installs (or removes) the Dragonfly Notebook plugin for every Dragonfly installation
# on this user account. Per-user location: no admin rights required and
# nothing under "C:\Program Files\Dragonfly" is touched.
#
#   Install:     powershell -ExecutionPolicy Bypass -File Install.ps1
#   Uninstall:   powershell -ExecutionPolicy Bypass -File Install.ps1 -Uninstall
#
param([switch]$Uninstall)

$pluginName = 'DragonflyNotebook'
$src = Join-Path $PSScriptRoot $pluginName

function Stop-WithError([string]$message) {
    Write-Warning $message
    # Launched via right-click "Run with PowerShell", the console closes the
    # instant the script ends; hold it open so the error is readable.
    Write-Host 'Press Enter to close this window...'
    [void](Read-Host)
    exit 1
}

# Plugins load at startup only: a plugin copied into the folder while
# Dragonfly runs stays invisible until restart, so installing over a live
# app gives the user a silently stale plugin. Refuse up front (uninstall
# keeps the reference behavior: the copy/remove error surfaces there).
if (-not $Uninstall) {
    $running = Get-Process -Name 'Dragonfly' -ErrorAction SilentlyContinue
    if ($running) {
        Stop-WithError 'Dragonfly is running - close it first. Plugins load at startup only: one copied in while Dragonfly runs is invisible until restart.'
    }
}

$parentDirs = @(
    (Join-Path $env:LOCALAPPDATA 'Comet'),
    (Join-Path $env:LOCALAPPDATA 'ORS'),
    $env:LOCALAPPDATA
)
# Only folders that actually carry a pythonUserExtensions tree are Dragonfly
# data roots: the plugin's own per-user registry folder
# (%LOCALAPPDATA%\DragonflyNotebook) matches the same 'Dragonfly*' filter and
# must NOT receive an install copy.
$dataRoots = $parentDirs | ForEach-Object {
    Get-ChildItem $_ -Directory -Filter 'Dragonfly*' -ErrorAction SilentlyContinue
} | Where-Object { Test-Path (Join-Path $_.FullName 'pythonUserExtensions') } |
    Sort-Object -Property FullName -Unique
if (-not $dataRoots) {
    Stop-WithError 'No Dragonfly data folder found under %LOCALAPPDATA% (checked %LOCALAPPDATA%\Comet\Dragonfly*, %LOCALAPPDATA%\ORS\Dragonfly* and %LOCALAPPDATA%\Dragonfly*). Open Dragonfly once (it creates the folder), then run this script again.'
}

foreach ($root in $dataRoots) {
    $pluginsDir = Join-Path $root.FullName 'pythonUserExtensions\Plugins'
    $dest = Join-Path $pluginsDir $pluginName

    if ($Uninstall) {
        if (Test-Path $dest) {
            try {
                Remove-Item -Recurse -Force -ErrorAction Stop $dest
                Write-Host "Removed: $dest"
            } catch {
                Stop-WithError "Could not remove $dest - is Dragonfly running? ($($_.Exception.Message))"
            }
        } else {
            Write-Host "Not installed (nothing removed): $dest"
        }
        continue
    }

    if (-not (Test-Path (Join-Path $src 'DragonflyNotebook.py'))) {
        Stop-WithError "Plugin source not found next to this script (expected folder '$pluginName' containing DragonflyNotebook.py): $src"
    }
    if (-not (Test-Path $pluginsDir)) {
        try {
            New-Item -ItemType Directory -Path $pluginsDir -ErrorAction Stop | Out-Null
        } catch {
            Stop-WithError "Could not create $pluginsDir ($($_.Exception.Message))"
        }
    }
    if (Test-Path $dest) {
        try {
            Remove-Item -Recurse -Force -ErrorAction Stop $dest
        } catch {
            Stop-WithError "Could not remove the previous install at $dest - is Dragonfly running? ($($_.Exception.Message))"
        }
    }
    try {
        Copy-Item -Recurse -ErrorAction Stop $src $dest
    } catch {
        Stop-WithError "Could not copy to $dest ($($_.Exception.Message))"
    }
    $pycache = Join-Path $dest '__pycache__'
    if (Test-Path $pycache) { Remove-Item -Recurse -Force $pycache }
    Write-Host "Installed: $dest"
}

Write-Host ''
Write-Host 'Restart Dragonfly, then open Utilities > Plugins > Run Dragonfly Notebook'
