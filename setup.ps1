<#
.SYNOPSIS
    One-time setup for KometaUI on Windows.

.DESCRIPTION
    Creates the backend virtual environment, installs both dependency sets, and builds the
    frontend. Safe to re-run; it will reuse an existing venv.

.PARAMETER KometaSource
    Path to a Kometa checkout. Optional but recommended: with it, KometaUI validates using
    Kometa's own validator instead of the bundled JSON schemas. Kometa is only ever read —
    KometaUI never runs it.

.EXAMPLE
    .\setup.ps1 -KometaSource C:\Projects\KometaSource
#>
[CmdletBinding()]
param(
    [string]$KometaSource
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

function Assert-Command($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$name was not found on PATH. $hint"
    }
}

Assert-Command python 'Install Python 3.12 or newer from https://python.org and re-open PowerShell.'
Assert-Command npm    'Install Node 20 or newer from https://nodejs.org and re-open PowerShell.'

$pythonVersion = (python -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if ([version]$pythonVersion -lt [version]'3.12') {
    throw "Python 3.12+ is required; found $pythonVersion."
}

Write-Host "`n[1/3] Backend virtual environment" -ForegroundColor Cyan
$venv = Join-Path $root 'backend\.venv'
if (-not (Test-Path $venv)) {
    python -m venv $venv
}
$venvPython = Join-Path $venv 'Scripts\python.exe'

& $venvPython -m pip install --quiet --upgrade pip
# The `kometa` extra pulls the few small packages Kometa's validator imports.
& $venvPython -m pip install --quiet -e "$(Join-Path $root 'backend')[dev,kometa]"

Write-Host "[2/3] Frontend dependencies" -ForegroundColor Cyan
Push-Location (Join-Path $root 'frontend')
try {
    if (Test-Path 'node_modules') { npm install --silent } else { npm ci --silent }

    Write-Host "[3/3] Building the frontend" -ForegroundColor Cyan
    # A production build is required: Vite's dev server cannot construct monaco-yaml's
    # web worker, which is what provides in-editor completion and inline errors.
    npm run build 2>&1 | Select-Object -Last 3
}
finally {
    Pop-Location
}

if ($KometaSource) {
    if (-not (Test-Path (Join-Path $KometaSource 'modules\validator.py'))) {
        Write-Warning "$KometaSource does not look like a Kometa checkout; the bundled schemas will be used instead."
    }
    else {
        $envFile = Join-Path $root 'backend\.env'
        "KOMETAUI_KOMETA_SOURCE_PATH=$KometaSource" | Set-Content -Path $envFile -Encoding utf8
        Write-Host "`nSaved Kometa source path to backend\.env" -ForegroundColor Green
    }
}

Write-Host "`nSetup complete. Start it with:  .\start.ps1" -ForegroundColor Green
