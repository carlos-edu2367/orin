[CmdletBinding()]
param(
    [string]$Version = 'latest',
    [switch]$NoDesktopShortcut,
    [switch]$Force,
    [switch]$Uninstall,
    [int]$WaitForPid = 0
)

$ErrorActionPreference = 'Stop'
$repository = 'carlos-edu2367/orin'
$programsRoot = Join-Path $env:LOCALAPPDATA 'Programs\Orin'
$stateRoot = Join-Path $env:LOCALAPPDATA 'Orin'
$binRoot = Join-Path $stateRoot 'bin'
$shim = Join-Path $binRoot 'orin.cmd'
$desktopLauncher = Join-Path $binRoot 'orin-desktop.ps1'
$legacyDesktopLauncher = Join-Path $binRoot 'orin-desktop.vbs'

function Set-UserPathEntry([string]$Directory, [bool]$Remove) {
    $existing = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($existing -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ne $Directory.TrimEnd('\') })
    if (-not $Remove) { $entries += $Directory }
    [Environment]::SetEnvironmentVariable('Path', ($entries -join ';'), 'User')
    if (-not $Remove) { $env:Path = "$Directory;$env:Path" }
}

function Get-Manifest([string]$RequestedVersion) {
    $asset = if ($RequestedVersion -eq 'latest') {
        'latest/download/release.json'
    }
    else {
        $normalized = $RequestedVersion.Trim().TrimStart('v')
        if ($normalized -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$') {
            throw 'Version must use semantic version format, for example 0.1.0.'
        }
        "download/v$normalized/release.json"
    }
    $uri = "https://github.com/$repository/releases/$asset"
    return Invoke-RestMethod -Uri $uri -MaximumRedirection 3
}

function Get-DesktopPath {
    return [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
}

function Start-DeferredRemoval([int]$ProcessId) {
    $cleanup = Join-Path ([System.IO.Path]::GetTempPath()) ("orin-uninstall-{0}-{1}.ps1" -f $ProcessId, $PID)
    @'
param(
    [int]$WaitForPid,
    [string]$ProgramsRoot,
    [string]$StateRoot
)

$ErrorActionPreference = 'SilentlyContinue'
function Test-OrinRoot([string]$Actual, [string]$Relative) {
    $expected = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA $Relative)).TrimEnd('\')
    $candidate = [System.IO.Path]::GetFullPath($Actual).TrimEnd('\')
    return [string]::Equals($candidate, $expected, [System.StringComparison]::OrdinalIgnoreCase)
}

if (-not (Test-OrinRoot $ProgramsRoot 'Programs\Orin') -or -not (Test-OrinRoot $StateRoot 'Orin')) { exit 1 }
if ($WaitForPid -gt 0) {
    try { Wait-Process -Id $WaitForPid } catch {}
}
for ($attempt = 0; $attempt -lt 180; $attempt++) {
    Remove-Item -LiteralPath $ProgramsRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $StateRoot -Recurse -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $ProgramsRoot) -and -not (Test-Path -LiteralPath $StateRoot)) { exit 0 }
    Start-Sleep -Seconds 1
}
exit 1
'@ | Set-Content -LiteralPath $cleanup -Encoding utf8
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $cleanup,
        '-WaitForPid', $ProcessId, '-ProgramsRoot', $programsRoot, '-StateRoot', $stateRoot
    ) -WindowStyle Hidden
}

if ($Uninstall) {
    if (-not $Force) {
        $answer = Read-Host 'Completely remove Orin, including all local data and configuration? [y/N]'
        if ($answer -notmatch '^(?i:y|yes)$') { return }
    }
    Remove-Item -LiteralPath $shim -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $desktopLauncher -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $legacyDesktopLauncher -Force -ErrorAction SilentlyContinue
    $shortcut = Join-Path (Get-DesktopPath) 'Orin Desktop.lnk'
    Remove-Item -LiteralPath $shortcut -Force -ErrorAction SilentlyContinue
    Set-UserPathEntry $binRoot $true
    Start-DeferredRemoval $WaitForPid
    Write-Host 'Orin removal was scheduled. The runtime, local data and configuration will be removed after Orin exits.' -ForegroundColor Yellow
    return
}

$manifest = Get-Manifest $Version
foreach ($property in 'version', 'archive_url', 'archive_sha256') {
    if ([string]::IsNullOrWhiteSpace([string]$manifest.$property)) { throw "Release manifest is missing '$property'." }
}
$installVersion = [string]$manifest.version
if ($installVersion -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$') { throw 'Release manifest contains an invalid version.' }
if ([string]$manifest.archive_url -notlike "https://github.com/$repository/releases/download/*") { throw 'Release archive URL must belong to the official Orin release.' }
if ([string]$manifest.archive_sha256 -notmatch '^[A-Fa-f0-9]{64}$') { throw 'Release manifest contains an invalid SHA-256.' }
$target = Join-Path $programsRoot $installVersion
$staging = "$target.staging"
$download = Join-Path ([System.IO.Path]::GetTempPath()) "orin-$installVersion-$PID.zip"

New-Item -ItemType Directory -Force -Path $programsRoot, $binRoot | Out-Null
Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
try {
    Invoke-WebRequest -Uri $manifest.archive_url -OutFile $download -MaximumRedirection 3
    $actual = (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne ([string]$manifest.archive_sha256).ToLowerInvariant()) { throw 'Downloaded release hash does not match release.json.' }
    Expand-Archive -LiteralPath $download -DestinationPath $staging -Force
    $runtime = Join-Path $staging 'resources\runtime\orin.exe'
    $desktop = Join-Path $staging 'Orin Desktop.exe'
    if (-not (Test-Path $runtime) -or -not (Test-Path $desktop)) { throw 'Release archive does not contain the required Orin runtime.' }
    & $runtime --version
    if ($LASTEXITCODE -ne 0) { throw 'Downloaded orin.exe did not pass its version check.' }
    if (Test-Path $target) {
        if (-not $Force) { throw "Orin $installVersion is already installed. Use -Force to reinstall it." }
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    Move-Item -LiteralPath $staging -Destination $target
    $current = Join-Path $programsRoot 'current'
    Remove-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Junction -Path $current -Target $target | Out-Null
}
finally {
    Remove-Item -LiteralPath $download -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
}

@"
@echo off
"%LOCALAPPDATA%\Programs\Orin\current\resources\runtime\orin.exe" %*
"@ | Set-Content -LiteralPath $shim -Encoding Ascii

@'
$runtime = Join-Path $env:LOCALAPPDATA 'Programs\Orin\current\resources\runtime\orin.exe'
Start-Process -FilePath $runtime -ArgumentList '--desktop' -WindowStyle Hidden
'@ | Set-Content -LiteralPath $desktopLauncher -Encoding Ascii
Remove-Item -LiteralPath $legacyDesktopLauncher -Force -ErrorAction SilentlyContinue
Set-UserPathEntry $binRoot $false

$shortcut = Join-Path (Get-DesktopPath) 'Orin Desktop.lnk'
$updateDesktopShortcut = Test-Path -LiteralPath $shortcut
if (-not $updateDesktopShortcut -and -not $NoDesktopShortcut) {
    $answer = Read-Host 'Do you want to create a desktop shortcut that opens Orin Desktop automatically? [Y/n]'
    if ($answer -notmatch '^(?i:n|no)$') {
        $updateDesktopShortcut = $true
    }
}
if ($updateDesktopShortcut) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($shortcut)
    $link.TargetPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $link.Arguments = ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $desktopLauncher)
    $link.WorkingDirectory = $binRoot
    $link.IconLocation = (Join-Path $programsRoot 'current\Orin Desktop.exe')
    $link.Save()
}

Write-Host "Orin $installVersion is installed. Run 'orin' or open Orin Desktop." -ForegroundColor Green
