[CmdletBinding()]
param(
    [string]$Version
)

$ErrorActionPreference = 'Stop'

function Get-Sha256Hash {
    param([Parameter(Mandatory)][string]$Path)

    $getFileHash = Get-Command Get-FileHash -ErrorAction SilentlyContinue
    if ($null -ne $getFileHash) {
        return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        return ([System.BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $stream.Dispose()
        $sha256.Dispose()
    }
}

$root = Split-Path -Parent $PSScriptRoot
$desktopPackage = Get-Content (Join-Path $root 'desktop\package.json') -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace($Version)) { $Version = [string]$desktopPackage.version }
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$') {
    throw 'Version must use semantic version format, for example 0.1.0.'
}

$source = Join-Path $root 'desktop\dist\win-unpacked'
$runtime = Join-Path $source 'resources\runtime\orin.exe'
$desktop = Join-Path $source 'Orin Desktop.exe'
if (-not (Test-Path $runtime) -or -not (Test-Path $desktop)) {
    throw 'The Electron directory package is incomplete. Run scripts\build-windows.ps1 first.'
}

$output = Join-Path $root 'dist'
$archiveName = "Orin-$Version-windows-x64.zip"
$archive = Join-Path $output $archiveName
New-Item -ItemType Directory -Force -Path $output | Out-Null
Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $source '*') -DestinationPath $archive -CompressionLevel Optimal
$hash = Get-Sha256Hash -Path $archive

$manifest = @{
    version = $Version
    archive_url = "https://github.com/carlos-edu2367/orin/releases/download/v$Version/$archiveName"
    archive_sha256 = $hash
    release_url = "https://github.com/carlos-edu2367/orin/releases/tag/v$Version"
} | ConvertTo-Json
$manifestPath = Join-Path $output 'release.json'
[System.IO.File]::WriteAllText($manifestPath, $manifest, [System.Text.UTF8Encoding]::new($false))

Copy-Item -LiteralPath (Join-Path $root 'install.ps1') -Destination (Join-Path $output 'install.ps1') -Force
Write-Host "Release archive: $archive"
Write-Host "Manifest: $(Join-Path $output 'release.json')"
