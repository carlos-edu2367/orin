<#
.SYNOPSIS
Start the same standalone local runtime used by the `orin` command.
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$Desktop,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

$arguments = @('-m', 'agentos.launcher', '--port', "$Port")
if ($Desktop) { $arguments += '--desktop' }
elseif ($NoBrowser) { $arguments += '--no-browser' }

Set-Location $root
& $python @arguments
exit $LASTEXITCODE
