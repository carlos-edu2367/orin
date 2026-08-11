<#
.SYNOPSIS
Stop the AgentOS local stack started by run-local.ps1.
#>
[CmdletBinding()]
param([int]$Port = 8000)

$ErrorActionPreference = "SilentlyContinue"

# Nothing listening is the normal case when the stack is already down, and
# Get-NetTCPConnection reports that as a terminating CIM error.
try {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
} catch { }

# Match on the command line, not the executable name: the same worker can be
# started through .venv\Scripts\python.exe, the uv interpreter it re-execs, or a
# system python3.13.exe, and a leftover process from any of them will keep
# consuming jobs with stale code loaded.
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'agentos\.workers\.(publisher|chat)' -or $_.CommandLine -match 'agentos\.api\.asgi' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Write-Host "AgentOS local stack stopped." -ForegroundColor Yellow
