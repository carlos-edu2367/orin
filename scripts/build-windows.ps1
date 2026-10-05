[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) { throw 'Create the development virtual environment before building a release.' }

Push-Location $root
try {
    & npm.cmd --prefix frontend ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    & npm.cmd --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }

    & $python -m PyInstaller packaging\orin.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { throw 'Frozen runtime build failed.' }

    $frozenRuntime = Join-Path $root 'dist\runtime'
    # Chromium is an on-demand download now; the release must carry the driver
    # that fetches it, and must not carry the browser itself.
    $driver = Get-ChildItem -LiteralPath $frozenRuntime -Recurse -Directory -Filter 'driver' -ErrorAction SilentlyContinue |
        Where-Object { $_.Parent.Name -eq 'playwright' } | Select-Object -First 1
    if (-not $driver) { throw 'Frozen runtime was built without the Playwright driver.' }
    $chromium = Get-ChildItem -LiteralPath $frozenRuntime -Recurse -File -Filter 'chrome.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($chromium) { throw 'Frozen runtime unexpectedly bundles Chromium; it must stay an optional download.' }

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
