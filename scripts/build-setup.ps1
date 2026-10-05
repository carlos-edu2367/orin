<#
.SYNOPSIS
Builds dist\OrinSetup.exe, the graphical installer, and signs it when a certificate is configured.

.DESCRIPTION
Signing is optional so forks and local builds work. To sign, set:
  ORIN_CODESIGN_PFX        path to a .pfx certificate
  ORIN_CODESIGN_PASSWORD   its password
Without them the build is unsigned and Windows SmartScreen will warn on first run.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Create the development virtual environment before building the installer.' }

Push-Location $root
try {
    & $python -c "import tkinter" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'This Python has no tkinter, which the installer window needs.' }

    & $python -m PyInstaller packaging\setup.spec --noconfirm --clean --distpath dist --workpath build\setup
    if ($LASTEXITCODE -ne 0) { throw 'OrinSetup build failed.' }

    $setup = Join-Path $root 'dist\OrinSetup.exe'
    if (-not (Test-Path $setup)) { throw 'OrinSetup.exe was not produced.' }

    if ($env:ORIN_CODESIGN_PFX) {
        $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if (-not $signtool) {
            $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -like '*x64*' } | Select-Object -Last 1
        }
        if (-not $signtool) { throw 'ORIN_CODESIGN_PFX is set but signtool.exe was not found.' }
        $path = if ($signtool -is [System.Management.Automation.ApplicationInfo]) { $signtool.Source } else { $signtool.FullName }
        & $path sign /fd SHA256 /f $env:ORIN_CODESIGN_PFX /p $env:ORIN_CODESIGN_PASSWORD /tr http://timestamp.digicert.com /td SHA256 $setup
        if ($LASTEXITCODE -ne 0) { throw 'Signing OrinSetup.exe failed.' }
        Write-Host 'OrinSetup.exe signed.'
    }
    else {
        Write-Warning 'OrinSetup.exe is NOT signed (ORIN_CODESIGN_PFX is not set); SmartScreen will warn users.'
    }

    $hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "Installer: $setup"
    Write-Host "SHA-256:   $hash"
}
finally { Pop-Location }
