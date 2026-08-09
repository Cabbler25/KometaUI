<#
.SYNOPSIS
    Start KometaUI.

.DESCRIPTION
    Runs the backend, which also serves the built frontend, so there is one process and
    one URL. Run setup.ps1 first.

.PARAMETER Port
    Port to listen on. Defaults to 8770.

.PARAMETER Workspace
    Kometa config directory to open at startup. Optional; you can also pick one in the UI.

.PARAMETER AllowWrites
    Unlock writes immediately. Off by default so opening a live config cannot alter it by
    accident; you can also unlock from the header at any time.

.PARAMETER NoBrowser
    Do not open a browser window.

.EXAMPLE
    .\start.ps1 -Workspace "C:\Users\me\Plex Meta Manager\Plex-Meta-Manager\config"
#>
[CmdletBinding()]
param(
    [int]$Port = 8770,
    [string]$Workspace,
    [switch]$AllowWrites,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venvPython = Join-Path $root 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    throw "Backend environment missing. Run .\setup.ps1 first."
}

$static = Join-Path $root 'backend\static'
$dist = Join-Path $root 'frontend\dist'
if (-not (Test-Path $static)) {
    if (-not (Test-Path $dist)) {
        throw "Frontend has not been built. Run .\setup.ps1 first."
    }
    # The backend serves whatever is in backend\static; mirror the build into it so the
    # whole app runs from a single process on a single port.
    Copy-Item -Recurse -Force $dist $static
}

if ($Workspace) { $env:KOMETAUI_WORKSPACE_PATH = $Workspace }
if ($AllowWrites) { $env:KOMETAUI_ALLOW_WRITES = 'true' }

$url = "http://127.0.0.1:$Port"

# Check the port before uvicorn does, so the failure is a sentence rather than a traceback.
$inUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    throw "Port $Port is already in use. Pass a different one, e.g. .\start.ps1 -Port 8771"
}

Write-Host "KometaUI  ->  $url" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop.`n" -ForegroundColor DarkGray

if (-not $NoBrowser) {
    # Give uvicorn a moment to bind before the browser asks for the page.
    Start-Job -ScriptBlock {
        param($target)
        Start-Sleep -Seconds 2
        Start-Process $target
    } -ArgumentList $url | Out-Null
}

Push-Location (Join-Path $root 'backend')
try {
    & $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
}
finally {
    Pop-Location
}
