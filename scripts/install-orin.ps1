<#
.SYNOPSIS
Put `orin` on your PATH so it works from any terminal, in any directory.

.DESCRIPTION
This is the development-profile installer. It registers a small shim that points
at the runtime in this checkout; it does not copy or build anything, so `git
pull` is immediately reflected in the `orin` command.

It is deliberately shaped like the future public installer:

    irm https://orin.dev/install.ps1 | iex

Both write the same shim to the same per-user bin directory and put that
directory on PATH. The only difference is where the runtime comes from — a
downloaded release archive there, this checkout here. Nothing else about the
launcher, the paths or the command changes between the two.

.EXAMPLE
.\scripts\install-orin.ps1

.EXAMPLE
.\scripts\install-orin.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$repository = Split-Path -Parent $PSScriptRoot
$binDirectory = Join-Path $env:LOCALAPPDATA "Orin\bin"
$shim = Join-Path $binDirectory "orin.cmd"

function Update-UserPath([string]$directory, [switch]$Remove) {
    # The user PATH only; never the machine PATH. Installing Orin must not
    # require an administrator and must not affect anyone else on the machine.
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @($current -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ne $directory.TrimEnd('\') })
    if (-not $Remove) { $entries += $directory }
    [Environment]::SetEnvironmentVariable("Path", ($entries -join ';'), "User")
}

if ($Uninstall) {
    if (Test-Path $shim) { Remove-Item $shim -Force }
    Update-UserPath $binDirectory -Remove
    Write-Host "orin removed from PATH. Your data, config and logs were not touched." -ForegroundColor Yellow
    return
}

$python = Join-Path $repository ".venv\Scripts\python.exe"
$installed = Join-Path $repository ".venv\Scripts\orin.exe"
if (-not (Test-Path $python)) {
    throw "No virtual environment at $repository\.venv. Create it first, then re-run this script."
}
if (-not (Test-Path $installed)) {
    Write-Host "Registering the orin entry point..." -ForegroundColor Cyan
    & $python -m pip install -e $repository --no-deps
    if ($LASTEXITCODE -ne 0) { throw "pip install -e failed with exit code $LASTEXITCODE" }
}

New-Item -ItemType Directory -Force -Path $binDirectory | Out-Null

# A shim rather than a copy: the command always resolves to the current runtime,
# and a future `orin update` replaces the runtime without rewriting this file.
@"
@echo off
"$installed" %*
"@ | Set-Content -Path $shim -Encoding ascii

$onPath = ([Environment]::GetEnvironmentVariable("Path", "User") -split ';') |
    Where-Object { $_.TrimEnd('\') -eq $binDirectory.TrimEnd('\') }
if (-not $onPath) { Update-UserPath $binDirectory }

Write-Host ""
Write-Host "Orin installed." -ForegroundColor Green
Write-Host "  command   $shim"
Write-Host "  runtime   $installed"
Write-Host ""
Write-Host "Open a new terminal and run:" -ForegroundColor Cyan
Write-Host "  orin"
Write-Host ""
