[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$browserRoot = Join-Path $root 'build\playwright'

if (-not (Test-Path $python)) { throw 'Create the development virtual environment before building a release.' }

Push-Location $root
try {
    & npm.cmd --prefix frontend ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    & npm.cmd --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }

    $env:PLAYWRIGHT_BROWSERS_PATH = $browserRoot
    & $python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Chromium provisioning failed.' }
    $env:ORIN_PLAYWRIGHT_BROWSERS_PATH = $browserRoot

    & $python -m PyInstaller packaging\orin.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { throw 'Frozen runtime build failed.' }

    if (-not $SkipTests) {
        & $python -m pytest -q tests\unit
        if ($LASTEXITCODE -ne 0) { throw 'Python unit tests failed.' }
    }

    Push-Location desktop
    try {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Electron dependency installation failed.' }
        & npm.cmd run build:dir
        if ($LASTEXITCODE -ne 0) { throw 'Electron package build failed.' }
    }
    finally { Pop-Location }

    & (Join-Path $PSScriptRoot 'package-release.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Release archive assembly failed.' }
}
finally { Pop-Location }
