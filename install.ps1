<#
.SYNOPSIS
One-line Orin installer: downloads OrinSetup.exe from the release and runs it without a window.

.DESCRIPTION
All installation logic (download, SHA-256 check, staged extraction, smoke tests,
rollback, the `orin` command, shortcuts) lives in the Orin installer engine, the
same one behind `orin update` and the OrinSetup window. This script only fetches
and starts it:

    irm https://github.com/carlos-edu2367/orin/releases/latest/download/install.ps1 | iex

To remove Orin, run `orin --uninstall`, or pass -Uninstall here.
#>
[CmdletBinding()]
param(
    [string]$Version = 'latest',
    [switch]$NoDesktopShortcut,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$repository = 'carlos-edu2367/orin'
$base = if ($env:ORIN_RELEASE_BASE_URL) { $env:ORIN_RELEASE_BASE_URL.TrimEnd('/') } else { "https://github.com/$repository/releases" }

if ($Uninstall) {
    $orin = Join-Path $env:LOCALAPPDATA 'Orin\bin\orin.cmd'
    if (-not (Test-Path -LiteralPath $orin)) { throw 'Orin does not appear to be installed (orin.cmd was not found).' }
    & $orin --uninstall
    return
}

$arguments = @('--silent')
if ($Version -eq 'latest') {
    $url = "$base/latest/download/OrinSetup.exe"
}
else {
    $normalized = $Version.Trim().TrimStart('v')
    if ($normalized -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$') { throw 'Version must use semantic version format, for example 0.5.0.' }
    $url = "$base/download/v$normalized/OrinSetup.exe"
    $arguments += @('--to', $normalized)
}
if ($NoDesktopShortcut) { $arguments += '--no-shortcut' }

$setup = Join-Path ([System.IO.Path]::GetTempPath()) "OrinSetup-$PID.exe"
try {
    try { Invoke-WebRequest -Uri $url -OutFile $setup -MaximumRedirection 3 -UseBasicParsing }
    catch { throw "Could not download the Orin installer from $url ($($_.Exception.Message))." }
    # OrinSetup is a windowed program, so `&` would not wait for it; Start-Process does.
    $process = Start-Process -FilePath $setup -ArgumentList $arguments -Wait -NoNewWindow -PassThru
    if ($process.ExitCode -ne 0) { throw "The Orin installer failed (exit code $($process.ExitCode))." }
}
finally {
    Remove-Item -LiteralPath $setup -Force -ErrorAction SilentlyContinue
}
