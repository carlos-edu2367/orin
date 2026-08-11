<#
.SYNOPSIS
Start the whole AgentOS local stack: API, dispatch publisher, and chat worker.

The three processes are separate on purpose. HTTP only accepts and persists a
turn; the publisher moves it onto Redis; the worker calls the provider. Running
the worker inside the HTTP process would make a long turn block the API.
#>
[CmdletBinding()]
param(
    [string]$EnvFile = ".env.local",
    [int]$Port = 8000,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

if (-not (Test-Path $EnvFile)) {
    throw "$EnvFile not found. Copy .env.local.example to .env.local first."
}

# Load the env file into this session so the publisher and worker, which do not
# take an --env-file flag, see the same configuration as the API.
foreach ($line in Get-Content $EnvFile) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $name, $value = $line -split '=', 2
    Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim()
}

# Native tools write progress and warnings to stderr, which "Stop" would treat
# as a failure. Exit codes are the only reliable success signal here.
function Invoke-Checked([scriptblock]$command, [string]$label) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $command } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$label failed with exit code $LASTEXITCODE" }
}

if (-not $SkipBuild) {
    Write-Host "Building the web client..." -ForegroundColor Cyan
    Push-Location (Join-Path $root "frontend")
    try {
        if (-not (Test-Path "node_modules")) { Invoke-Checked { npm ci } "npm ci" }
        Invoke-Checked { npm run build } "npm run build"
    } finally { Pop-Location }
}

Write-Host "Applying database migrations..." -ForegroundColor Cyan
Invoke-Checked { & $python -m alembic upgrade head } "alembic upgrade head"

$logs = Join-Path $root ".logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

function Start-Component([string]$name, [string[]]$arguments) {
    $out = Join-Path $logs "$name.out.log"
    $err = Join-Path $logs "$name.err.log"
    Start-Process -FilePath $python -ArgumentList $arguments -NoNewWindow -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
}

$api = Start-Component "api" @("-m", "uvicorn", "--app-dir", "src", "agentos.api.asgi:app", "--env-file", $EnvFile, "--host", "127.0.0.1", "--port", "$Port", "--no-proxy-headers")
$publisher = Start-Component "publisher" @("-m", "agentos.workers.publisher")
$worker = Start-Component "worker" @("-m", "arq", "agentos.workers.chat.WorkerSettings")

Write-Host ""
Write-Host "AgentOS is starting:" -ForegroundColor Green
Write-Host "  API       pid $($api.Id)   http://127.0.0.1:$Port"
Write-Host "  publisher pid $($publisher.Id)"
Write-Host "  worker    pid $($worker.Id)"
Write-Host "  logs      $logs"
Write-Host ""
Write-Host "Stop everything with: .\scripts\stop-local.ps1" -ForegroundColor Yellow
