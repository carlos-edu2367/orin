<#
.SYNOPSIS
Install Orin from a development checkout.

.DESCRIPTION
Bootstraps everything required after cloning the repository:

    git clone https://github.com/carlos-edu2367/orin.git
    cd orin
    .\scripts\install-orin.ps1

The installer:

    - validates Python 3.13+
    - validates Node.js 20+
    - checks Docker availability
    - creates .venv when needed
    - installs/upgrades Python dependencies
    - creates/updates .env.local
    - generates AGENTOS_PROVIDER_ENCRYPTION_KEY when missing
    - creates/updates frontend/.env.local
    - installs frontend dependencies with npm ci
    - builds the frontend
    - registers the `orin` command on the user's PATH

The operation is idempotent and can safely be run again after `git pull`.

Existing environment values are preserved. Missing variables from the example
files are appended automatically.

.EXAMPLE
.\scripts\install-orin.ps1

.EXAMPLE
.\scripts\install-orin.ps1 -Uninstall
#>

[CmdletBinding()]
param(
    [switch]$Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

$repository = Split-Path -Parent $PSScriptRoot

$venvDirectory = Join-Path $repository ".venv"
$venvPython = Join-Path $venvDirectory "Scripts\python.exe"
$installedOrin = Join-Path $venvDirectory "Scripts\orin.exe"

$frontendDirectory = Join-Path $repository "frontend"

$rootEnvTemplate = Join-Path $repository ".env.local.example"
$rootEnv = Join-Path $repository ".env.local"

$frontendEnvTemplate = Join-Path $frontendDirectory ".env.local.example"
$frontendEnv = Join-Path $frontendDirectory ".env.local"

$binDirectory = Join-Path $env:LOCALAPPDATA "Orin\bin"
$shim = Join-Path $binDirectory "orin.cmd"


# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------

function Write-Step([string]$message) {
    Write-Host ""
    Write-Host "  -> $message" -ForegroundColor Cyan
}

function Write-Success([string]$message) {
    Write-Host "  OK $message" -ForegroundColor Green
}

function Write-WarningMessage([string]$message) {
    Write-Host "  !  $message" -ForegroundColor Yellow
}


# ---------------------------------------------------------------------------
# Command helpers
# ---------------------------------------------------------------------------

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [string[]]$Arguments = @(),

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    & $FilePath @Arguments

    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}


# ---------------------------------------------------------------------------
# PATH management
# ---------------------------------------------------------------------------

function Update-UserPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory,

        [switch]$Remove
    )

    $current = [Environment]::GetEnvironmentVariable("Path", "User")

    if ($null -eq $current) {
        $current = ""
    }

    $normalizedDirectory = $Directory.TrimEnd("\")

    $entries = @(
        $current -split ";" |
        Where-Object {
            $_ -and $_.TrimEnd("\") -ne $normalizedDirectory
        }
    )

    if (-not $Remove) {
        $entries += $Directory
    }

    [Environment]::SetEnvironmentVariable(
        "Path",
        ($entries -join ";"),
        "User"
    )
}


function Update-CurrentProcessPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory,

        [switch]$Remove
    )

    $normalizedDirectory = $Directory.TrimEnd("\")

    $entries = @(
        $env:Path -split ";" |
        Where-Object {
            $_ -and $_.TrimEnd("\") -ne $normalizedDirectory
        }
    )

    if (-not $Remove) {
        $entries = @($Directory) + $entries
    }

    $env:Path = $entries -join ";"
}


# ---------------------------------------------------------------------------
# UTF-8 helpers
# ---------------------------------------------------------------------------

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Content
    )

    $encoding = New-Object System.Text.UTF8Encoding($false)

    [System.IO.File]::WriteAllText(
        $Path,
        $Content,
        $encoding
    )
}


# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------

function Sync-EnvFromTemplate {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Template,

        [Parameter(Mandatory = $true)]
        [string]$Target
    )

    if (-not (Test-Path $Template)) {
        throw "Environment template not found: $Template"
    }

    if (-not (Test-Path $Target)) {
        Copy-Item $Template $Target
        Write-Success "Created $(Split-Path -Leaf $Target)"
        return
    }

    $targetContent = [System.IO.File]::ReadAllText($Target)
    $templateContent = [System.IO.File]::ReadAllText($Template)

    $existingKeys = @{}

    foreach ($line in ($targetContent -split '\r?\n')) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
            $existingKeys[$Matches[1]] = $true
        }
    }

    $missingLines = @()

    foreach ($line in ($templateContent -split '\r?\n')) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
            $key = $Matches[1]

            if (-not $existingKeys.ContainsKey($key)) {
                $missingLines += $line
                $existingKeys[$key] = $true
            }
        }
    }

    if ($missingLines.Count -eq 0) {
        Write-Success "$(Split-Path -Leaf $Target) already up to date"
        return
    }

    if ($targetContent.Length -gt 0 -and -not $targetContent.EndsWith("`n")) {
        $targetContent += [Environment]::NewLine
    }

    $targetContent += [Environment]::NewLine
    $targetContent += "# Added automatically by scripts/install-orin.ps1"
    $targetContent += [Environment]::NewLine
    $targetContent += ($missingLines -join [Environment]::NewLine)
    $targetContent += [Environment]::NewLine

    Write-Utf8NoBom -Path $Target -Content $targetContent

    Write-Success "Updated $(Split-Path -Leaf $Target)"
}


function Get-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Key
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    $escapedKey = [regex]::Escape($Key)

    foreach ($line in ([System.IO.File]::ReadAllLines($Path))) {
        if ($line -match "^\s*$escapedKey\s*=(.*)$") {
            return $Matches[1].Trim()
        }
    }

    return $null
}


function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Key,

        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $content = [System.IO.File]::ReadAllText($Path)

    $escapedKey = [regex]::Escape($Key)
    $pattern = "(?m)^\s*$escapedKey\s*=.*$"

    $newLine = "$Key=$Value"

    $regex = New-Object System.Text.RegularExpressions.Regex($pattern)

    if ($regex.IsMatch($content)) {
        $content = $regex.Replace(
            $content,
            $newLine,
            1
        )
    }
    else {
        if ($content.Length -gt 0 -and -not $content.EndsWith("`n")) {
            $content += [Environment]::NewLine
        }

        $content += $newLine
        $content += [Environment]::NewLine
    }

    Write-Utf8NoBom -Path $Path -Content $content
}


# ---------------------------------------------------------------------------
# Python discovery
# ---------------------------------------------------------------------------

function Get-Python313 {
    # First prefer the Windows Python launcher.
    $py = Get-Command "py.exe" -ErrorAction SilentlyContinue

    if (-not $py) {
        $py = Get-Command "py" -ErrorAction SilentlyContinue
    }

    if ($py) {
        # Prefer exactly 3.13 when installed.
        $candidate = & $py.Source -3.13 -c `
            "import sys; print(sys.executable)" 2>$null

        if ($LASTEXITCODE -eq 0 -and $candidate) {
            return ($candidate | Select-Object -Last 1).Trim()
        }

        # Otherwise any >= 3.13 is acceptable.
        $candidate = & $py.Source -3 -c `
            "import sys; print(sys.executable if sys.version_info >= (3, 13) else '')" `
            2>$null

        if ($LASTEXITCODE -eq 0 -and $candidate) {
            $result = ($candidate | Select-Object -Last 1).Trim()

            if ($result) {
                return $result
            }
        }
    }

    # Fall back to python/python3 on PATH.
    foreach ($name in @(
        "python.exe",
        "python",
        "python3.exe",
        "python3"
    )) {
        $command = Get-Command $name -ErrorAction SilentlyContinue

        if (-not $command) {
            continue
        }

        $candidate = & $command.Source -c `
            "import sys; print(sys.executable if sys.version_info >= (3, 13) else '')" `
            2>$null

        if ($LASTEXITCODE -eq 0 -and $candidate) {
            $result = ($candidate | Select-Object -Last 1).Trim()

            if ($result) {
                return $result
            }
        }
    }

    return $null
}


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

if ($Uninstall) {
    Write-Step "Removing Orin command"

    if (Test-Path $shim) {
        Remove-Item $shim -Force
    }

    Update-UserPath -Directory $binDirectory -Remove
    Update-CurrentProcessPath -Directory $binDirectory -Remove

    Write-Host ""
    Write-Host "Orin removed from PATH." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Your repository, virtual environment, configuration,"
    Write-Host "data and logs were not removed."
    Write-Host ""

    return
}


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "  ORIN" -ForegroundColor White
Write-Host ""
Write-Host "  Installing local development runtime..."
Write-Host ""


# ---------------------------------------------------------------------------
# Validate repository
# ---------------------------------------------------------------------------

Write-Step "Checking repository"

$requiredFiles = @(
    (Join-Path $repository "pyproject.toml"),
    (Join-Path $repository "frontend\package.json"),
    (Join-Path $repository "frontend\package-lock.json"),
    $rootEnvTemplate,
    $frontendEnvTemplate
)

foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path $requiredFile)) {
        throw "Required repository file not found: $requiredFile"
    }
}

Write-Success "Repository looks valid"


# ---------------------------------------------------------------------------
# Check Python
# ---------------------------------------------------------------------------

Write-Step "Checking Python"

$systemPython = Get-Python313

if (-not $systemPython) {
    throw @"
Python 3.13 or newer was not found.

Install Python 3.13+ and make it available through either:
  py
or:
  python

Then run this installer again.
"@
}

$pythonVersion = & $systemPython -c `
    "import sys; print('.'.join(map(str, sys.version_info[:3])))"

if ($LASTEXITCODE -ne 0) {
    throw "Unable to determine Python version."
}

Write-Success "Python $pythonVersion"


# ---------------------------------------------------------------------------
# Check Node
# ---------------------------------------------------------------------------

Write-Step "Checking Node.js"

$nodeCommand = Get-Command "node.exe" -ErrorAction SilentlyContinue

if (-not $nodeCommand) {
    $nodeCommand = Get-Command "node" -ErrorAction SilentlyContinue
}

if (-not $nodeCommand) {
    throw @"
Node.js 20 or newer was not found.

Install Node.js 20+ and run this installer again.
"@
}

$nodeVersion = (& $nodeCommand.Source --version).Trim().TrimStart("v")
$nodeMajor = [int]($nodeVersion.Split(".")[0])

if ($nodeMajor -lt 20) {
    throw "Node.js 20+ is required. Found Node.js $nodeVersion."
}

Write-Success "Node.js $nodeVersion"


# ---------------------------------------------------------------------------
# Check npm
# ---------------------------------------------------------------------------

Write-Step "Checking npm"

$npmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue

if (-not $npmCommand) {
    $npmCommand = Get-Command "npm" -ErrorAction SilentlyContinue
}

if (-not $npmCommand) {
    throw "npm was not found. Reinstall Node.js with npm included."
}

$npmVersion = (& $npmCommand.Source --version).Trim()

Write-Success "npm $npmVersion"


# ---------------------------------------------------------------------------
# Check Docker
# ---------------------------------------------------------------------------

Write-Step "Checking Docker"

$dockerCommand = Get-Command "docker.exe" -ErrorAction SilentlyContinue

if (-not $dockerCommand) {
    $dockerCommand = Get-Command "docker" -ErrorAction SilentlyContinue
}

if (-not $dockerCommand) {
    throw @"
Docker was not found.

Orin currently uses Docker Desktop to run PostgreSQL and Redis.

Install Docker Desktop and run this installer again.
"@
}

$dockerVersion = & $dockerCommand.Source --version

if ($LASTEXITCODE -ne 0) {
    throw "Docker is installed but could not be executed."
}

Write-Success $dockerVersion

# Docker Desktop does not need to be running to INSTALL Orin, but it will need
# to be running when `orin` starts PostgreSQL and Redis.
& $dockerCommand.Source info *> $null

if ($LASTEXITCODE -ne 0) {
    Write-WarningMessage "Docker Desktop is installed but is not currently running."
    Write-WarningMessage "Start Docker Desktop before running orin."
}
else {
    Write-Success "Docker engine is running"
}


# ---------------------------------------------------------------------------
# Virtual environment
# ---------------------------------------------------------------------------

Write-Step "Preparing Python environment"

if (-not (Test-Path $venvPython)) {
    Write-Host "  Creating .venv..."

    Invoke-Checked `
        -FilePath $systemPython `
        -Arguments @(
            "-m",
            "venv",
            $venvDirectory
        ) `
        -Description "Creating Python virtual environment"

    Write-Success "Created .venv"
}
else {
    Write-Success ".venv already exists"
}


# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------

Write-Step "Installing Python dependencies"

Invoke-Checked `
    -FilePath $venvPython `
    -Arguments @(
        "-m",
        "pip",
        "install",
        "--upgrade",
        "pip"
    ) `
    -Description "Upgrading pip"

Invoke-Checked `
    -FilePath $venvPython `
    -Arguments @(
        "-m",
        "pip",
        "install",
        "-e",
        $repository
    ) `
    -Description "Installing Orin Python dependencies"

if (-not (Test-Path $installedOrin)) {
    throw @"
Python dependencies were installed, but the Orin entry point was not created:

$installedOrin
"@
}

Write-Success "Python dependencies installed"


# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------

Write-Step "Preparing configuration"

Sync-EnvFromTemplate `
    -Template $rootEnvTemplate `
    -Target $rootEnv

Sync-EnvFromTemplate `
    -Template $frontendEnvTemplate `
    -Target $frontendEnv


# ---------------------------------------------------------------------------
# Encryption key
# ---------------------------------------------------------------------------

$encryptionKey = Get-EnvValue `
    -Path $rootEnv `
    -Key "AGENTOS_PROVIDER_ENCRYPTION_KEY"

if ([string]::IsNullOrWhiteSpace($encryptionKey)) {
    Write-Host "  Generating provider encryption key..."

    $generatedKey = & $venvPython -c `
        "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode('ascii'))"

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to generate AGENTOS_PROVIDER_ENCRYPTION_KEY."
    }

    $generatedKey = ($generatedKey | Select-Object -Last 1).Trim()

    if (-not $generatedKey) {
        throw "Generated encryption key was empty."
    }

    Set-EnvValue `
        -Path $rootEnv `
        -Key "AGENTOS_PROVIDER_ENCRYPTION_KEY" `
        -Value $generatedKey

    Write-Success "Generated AGENTOS_PROVIDER_ENCRYPTION_KEY"
}
else {
    Write-Success "Provider encryption key already configured"
}


# ---------------------------------------------------------------------------
# Frontend dependencies
# ---------------------------------------------------------------------------

Write-Step "Installing frontend dependencies"

Invoke-Checked `
    -FilePath $npmCommand.Source `
    -Arguments @(
        "--prefix",
        $frontendDirectory,
        "ci"
    ) `
    -Description "Installing frontend dependencies"

Write-Success "Frontend dependencies installed"


# ---------------------------------------------------------------------------
# Frontend build
# ---------------------------------------------------------------------------

Write-Step "Building frontend"

Invoke-Checked `
    -FilePath $npmCommand.Source `
    -Arguments @(
        "--prefix",
        $frontendDirectory,
        "run",
        "build"
    ) `
    -Description "Building frontend"

$frontendDist = Join-Path $frontendDirectory "dist"

if (-not (Test-Path $frontendDist)) {
    throw "Frontend build completed but frontend/dist was not created."
}

Write-Success "Frontend built"


# ---------------------------------------------------------------------------
# Install global command shim
# ---------------------------------------------------------------------------

Write-Step "Registering orin command"

New-Item `
    -ItemType Directory `
    -Force `
    -Path $binDirectory |
    Out-Null

@"
@echo off
"$installedOrin" %*
"@ |
    Set-Content `
        -Path $shim `
        -Encoding ascii


# User PATH persists across terminals.
Update-UserPath -Directory $binDirectory

# Process PATH lets `orin` work immediately in this same terminal as well.
Update-CurrentProcessPath -Directory $binDirectory


Write-Success "Registered $shim"


# ---------------------------------------------------------------------------
# Finished
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "  ORIN" -ForegroundColor White
Write-Host ""
Write-Host "  Installation complete." -ForegroundColor Green
Write-Host ""
Write-Host "  Runtime     $installedOrin"
Write-Host "  Config      $rootEnv"
Write-Host "  Frontend    $frontendDist"
Write-Host "  Command     $shim"
Write-Host ""
Write-Host "  Run:" -ForegroundColor Cyan
Write-Host ""
Write-Host "      orin" -ForegroundColor White
Write-Host ""

if ($LASTEXITCODE -ne 0) {
    # $LASTEXITCODE may still contain the docker-info result when Docker Desktop
    # was installed but stopped. Do not allow that optional check to make the
    # installer itself exit unsuccessfully.
    $global:LASTEXITCODE = 0
}
