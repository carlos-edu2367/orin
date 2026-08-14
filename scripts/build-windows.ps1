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

    $frozenRuntime = Join-Path $root 'dist\runtime'
    $bundledBrowserRoots = @(
        (Join-Path $frozenRuntime 'playwright'),
        (Join-Path $frozenRuntime '_internal\playwright')
    ) | Where-Object { Test-Path $_ -PathType Container }
    if (-not $bundledBrowserRoots) { throw 'Frozen runtime was built without the bundled Playwright browser directory.' }
    $chromium = $bundledBrowserRoots | ForEach-Object {
        Get-ChildItem -LiteralPath $_ -Recurse -File -Filter 'chrome.exe' -ErrorAction SilentlyContinue
    } | Select-Object -First 1
    if (-not $chromium) { throw 'Frozen runtime was built without a Chromium executable.' }
    Write-Host "Bundled Chromium: $($chromium.FullName)"

    if (-not $SkipTests) {
        # This variable is only an input to the PyInstaller spec. Do not let it
        # override the frozen-layout assertions while running the test suite.
        $packagingBrowserPath = $env:ORIN_PLAYWRIGHT_BROWSERS_PATH
        $playwrightBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
        Remove-Item Env:ORIN_PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
        Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
        try {
            & $python -m pytest -q tests\unit
            if ($LASTEXITCODE -ne 0) { throw 'Python unit tests failed.' }
        }
        finally {
            if ($null -ne $packagingBrowserPath) { $env:ORIN_PLAYWRIGHT_BROWSERS_PATH = $packagingBrowserPath }
            if ($null -ne $playwrightBrowserPath) { $env:PLAYWRIGHT_BROWSERS_PATH = $playwrightBrowserPath }
        }
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
