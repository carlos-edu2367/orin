# Orin Installers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One line installs Orin on Windows, macOS and Linux, and a CI matrix proves it on all three before any human runs it.

**Architecture:** Both installers do the same six things in the same order: resolve the release, ensure `uv`, install Python 3.13, install the wheel into `versions/<version>` and repoint `current`, fetch and verify PostgreSQL, write the shim and the PATH entry. `orin update` re-runs the very same script rather than reimplementing it in Python, so there is one install path and the smoke matrix tests it twice.

**Tech Stack:** POSIX `sh`, Windows PowerShell 5.1+, `uv`, GitHub Actions.

This is part three of three. **Parts one and two must be merged first**: part one removes the Docker prerequisite the installer cannot satisfy, and part two publishes the assets the installer downloads.

Spec: [`docs/superpowers/specs/2026-08-11-orin-distribution-design.md`](../specs/2026-08-11-orin-distribution-design.md)

## Global Constraints

- **Never require administrator or `sudo`.** User PATH only, user directories only.
- **Verify before unpacking.** A failed checksum leaves nothing installed and no PATH entry.
- **Fail closed.** `set -eu` in `sh`; `$ErrorActionPreference = "Stop"` in PowerShell. A partial installation is worse than none.
- **No hardcoded domain.** `ORIN_INSTALL_BASE` defaults to `https://github.com/carlos-edu2367/orin/releases/latest/download` and is the single point the future domain replaces.
- Asset names are fixed by part two: `manifest.json`, the wheel named inside it, `postgres-16-<tag>.txz`, `SHA256SUMS`.
- Layout, identical in both scripts (POSIX / Windows):
  - versions `~/.local/share/orin/app/versions/<v>` / `%LOCALAPPDATA%\Programs\Orin\versions\<v>`
  - pointer `~/.local/share/orin/app/current` / `%LOCALAPPDATA%\Programs\Orin\current`
  - command `~/.local/bin/orin` / `%LOCALAPPDATA%\Orin\bin\orin.cmd`
  - PostgreSQL `~/.local/share/orin/runtime/postgres/16` / `%LOCALAPPDATA%\Orin\runtime\postgres\16`
- Uninstalling never deletes data, configuration or logs, and prints where each one is.
- POSIX `sh`, not `bash`: no arrays, no `[[`, no `local` outside functions that declare it portably.

## File Structure

**Created**

| File | Responsibility |
| --- | --- |
| `install.sh` | The macOS and Linux installer. Repository root, because that is where a raw URL is shortest. |
| `install.ps1` | The Windows installer. |
| `src/agentos/installation/layout.py` | Where an installation's own directories are, as opposed to where its state is. Read by `orin update` and `orin uninstall`. |
| `src/agentos/launcher/maintenance.py` | The `update` and `uninstall` verbs. |
| `.github/workflows/install-smoke.yml` | Runs the real installers on all three operating systems. |
| `docs/INSTALL.md` | Installing, updating, uninstalling, and the domain cutover. |
| `docs/adr/0001-remove-redis.md` | Why the queue is a table. |
| `docs/adr/0002-embed-postgres.md` | Why Orin ships its own database. |
| `tests/unit/installation/test_layout.py` | Layout resolution per platform. |
| `tests/unit/launcher/test_maintenance.py` | Uninstall removes the right things and spares the user's data. |

**Modified**

| File | Change |
| --- | --- |
| `src/agentos/installation/__init__.py` | Export `InstallationLayout`. |
| `src/agentos/launcher/cli.py` | `update` and `uninstall` subcommands. |
| `docs/LAUNCHER.md` | Point at `docs/INSTALL.md`. |
| `README.md` | The one-liner replaces the manual build. |
| `scripts/install-orin.ps1` | A note that it is the development installer, and that `install.ps1` is the public one. |

---

### Task 1: Know where an installation lives

`OrinPaths` answers "where does Orin write". Nothing answers "where is Orin installed" — which is what updating and uninstalling need, and what must never be confused with the first.

**Files:**
- Create: `src/agentos/installation/layout.py`, `tests/unit/installation/test_layout.py`
- Modify: `src/agentos/installation/__init__.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `InstallationLayout(root: Path, versions: Path, current: Path, bin_dir: Path, command: Path)` — frozen, slots
  - `InstallationLayout.resolve() -> InstallationLayout`
  - `InstallationLayout.installed_versions() -> tuple[str, ...]`
  - `InstallationLayout.is_installed() -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/installation/test_layout.py`:

```python
from __future__ import annotations

from pathlib import Path

from agentos.installation.layout import InstallationLayout


def test_windows_keeps_the_installation_apart_from_the_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("agentos.installation.layout.os.name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

    layout = InstallationLayout.resolve()

    assert layout.root == tmp_path / "Local" / "Programs" / "Orin"
    assert layout.versions == layout.root / "versions"
    assert layout.current == layout.root / "current"
    # The command lives with the state, not with the versions: repointing an
    # installation must never mean rewriting the thing that is on PATH.
    assert layout.bin_dir == tmp_path / "Local" / "Orin" / "bin"
    assert layout.command == layout.bin_dir / "orin.cmd"


def test_posix_follows_xdg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("agentos.installation.layout.os.name", "posix")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr("agentos.installation.layout.Path.home", lambda: tmp_path)

    layout = InstallationLayout.resolve()

    assert layout.root == tmp_path / "share" / "orin" / "app"
    assert layout.command == tmp_path / ".local" / "bin" / "orin"


def test_versions_are_listed_newest_last(tmp_path: Path) -> None:
    layout = InstallationLayout(tmp_path, tmp_path / "versions", tmp_path / "current", tmp_path / "bin", tmp_path / "bin" / "orin")
    for version in ("0.2.0", "0.10.0", "0.1.0"):
        (layout.versions / version).mkdir(parents=True)

    assert layout.installed_versions() == ("0.1.0", "0.2.0", "0.10.0")


def test_an_installation_with_no_pointer_is_not_installed(tmp_path: Path) -> None:
    layout = InstallationLayout(tmp_path, tmp_path / "versions", tmp_path / "current", tmp_path / "bin", tmp_path / "bin" / "orin")

    assert layout.is_installed() is False
```

- [ ] **Step 2: Run them and verify they fail**

```bash
python -m pytest tests/unit/installation/test_layout.py -q
```

Expected: `ModuleNotFoundError: No module named 'agentos.installation.layout'`.

- [ ] **Step 3: Write the module**

Create `src/agentos/installation/layout.py`:

```python
"""Where an Orin installation lives, as opposed to where it writes.

``OrinPaths`` answers the second question and deliberately keeps its answers
outside this layout. Keeping them apart is the whole reason an update can
replace a version, and a rollback can put the old one back, without either
touching a conversation, a workspace or a key.

The command on PATH is a shim in the *state* directory pointing at ``current``,
not a file inside a version. Repointing an installation therefore never rewrites
the thing the user's shell resolved.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _windows_local() -> Path:
    base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Local"


def _version_key(name: str) -> tuple:
    parts = []
    for chunk in name.replace("-", ".").split("."):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
    return tuple(parts)


@dataclass(frozen=True, slots=True)
class InstallationLayout:
    root: Path
    versions: Path
    current: Path
    bin_dir: Path
    command: Path

    @classmethod
    def resolve(cls) -> "InstallationLayout":
        if os.name == "nt":
            local = _windows_local()
            root = local / "Programs" / "Orin"
            bin_dir = local / "Orin" / "bin"
            command = bin_dir / "orin.cmd"
        else:
            data = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "orin"
            root = data / "app"
            bin_dir = Path.home() / ".local" / "bin"
            command = bin_dir / "orin"
        return cls(root=root, versions=root / "versions", current=root / "current", bin_dir=bin_dir, command=command)

    def installed_versions(self) -> tuple[str, ...]:
        try:
            names = [entry.name for entry in self.versions.iterdir() if entry.is_dir()]
        except OSError:
            return ()
        return tuple(sorted(names, key=_version_key))

    def is_installed(self) -> bool:
        return self.current.exists() and self.command.exists()


__all__ = ["InstallationLayout"]
```

- [ ] **Step 4: Export it**

Add to `src/agentos/installation/__init__.py`, following the existing export style:

```python
from .layout import InstallationLayout
```

and add `"InstallationLayout"` to `__all__`.

- [ ] **Step 5: Run the tests**

```bash
python -m pytest tests/unit/installation -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/installation tests/unit/installation/test_layout.py
git commit -m "feat(installation): describe where an installation lives"
```

---

### Task 2: The macOS and Linux installer

**Files:**
- Create: `install.sh`

**Interfaces:**
- Consumes: `manifest.json`, the wheel, `postgres-16-<tag>.txz` and `SHA256SUMS` from the release built in part two.
- Produces: the layout from Task 1, and an `orin` command on PATH. Accepts `--version <v>`, `--no-modify-path`, `--uninstall`, and honours `ORIN_INSTALL_BASE`.

- [ ] **Step 1: Write the script**

Create `install.sh` at the repository root:

```sh
#!/bin/sh
# Install Orin on macOS or Linux.
#
#   curl -fsSL https://github.com/carlos-edu2367/orin/releases/latest/download/install.sh | sh
#
# No sudo, no system packages: everything lands under your home directory, and
# uninstalling removes exactly what was added. Set ORIN_INSTALL_BASE to install
# from somewhere other than the latest GitHub release.
set -eu

BASE="${ORIN_INSTALL_BASE:-https://github.com/carlos-edu2367/orin/releases/latest/download}"
VERSION=""
MODIFY_PATH=1
UNINSTALL=0

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/orin"
APP_ROOT="$DATA_HOME/app"
VERSIONS="$APP_ROOT/versions"
CURRENT="$APP_ROOT/current"
BIN_DIR="$HOME/.local/bin"
COMMAND="$BIN_DIR/orin"
PG_ROOT="$DATA_HOME/runtime/postgres/16"

say() { printf '%s\n' "$*"; }
die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required but not installed."; }

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:-}"; [ -n "$VERSION" ] || die "--version needs a value"; shift 2 ;;
    --no-modify-path) MODIFY_PATH=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) say "usage: install.sh [--version X.Y.Z] [--no-modify-path] [--uninstall]"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

if [ -n "$VERSION" ]; then
  BASE="$(printf '%s' "$BASE" | sed 's#/releases/latest/download#/releases/download/v'"$VERSION"'#')"
fi

# -- uninstall ---------------------------------------------------------------

if [ "$UNINSTALL" -eq 1 ]; then
  rm -f "$COMMAND"
  rm -rf "$APP_ROOT"
  for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
    [ -f "$rc" ] || continue
    # Only the block this installer wrote, matched by its markers.
    sed -i.orin-backup '/# >>> orin >>>/,/# <<< orin <<</d' "$rc" 2>/dev/null || \
      sed -i '' '/# >>> orin >>>/,/# <<< orin <<</d' "$rc" 2>/dev/null || true
    rm -f "$rc.orin-backup"
  done
  say ""
  say "Orin removed. Your data was not touched:"
  say "  data    $DATA_HOME/data"
  say "  config  ${XDG_CONFIG_HOME:-$HOME/.config}/orin"
  say "  logs    $DATA_HOME/logs"
  say "  database $DATA_HOME/data/postgres"
  say ""
  say "Delete them yourself if you want them gone."
  exit 0
fi

# -- platform ----------------------------------------------------------------

need curl
need tar
system="$(uname -s)"
machine="$(uname -m)"
case "$system:$machine" in
  Darwin:arm64)          TAG="darwin-arm64" ;;
  Darwin:x86_64)         TAG="darwin-amd64" ;;
  Linux:x86_64|Linux:amd64) TAG="linux-amd64" ;;
  Linux:aarch64|Linux:arm64) TAG="linux-arm64" ;;
  *) die "Orin has no build for $system on $machine.
Supported: macOS arm64 and x86_64, Linux x86_64 and arm64." ;;
esac

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else die "no sha256sum or shasum on this system; cannot verify the download."
  fi
}

WORK="$(mktemp -d)"
# A failed install must leave nothing behind, including on Ctrl+C.
trap 'rm -rf "$WORK"' EXIT INT TERM

say ""
say "Installing Orin for $TAG"

# -- uv ----------------------------------------------------------------------

if command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
else
  say "  installing uv"
  curl -fsSL https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1 \
    || die "could not install uv from https://astral.sh/uv/install.sh"
  UV="$HOME/.local/bin/uv"
  [ -x "$UV" ] || UV="$HOME/.cargo/bin/uv"
  [ -x "$UV" ] || die "uv installed but could not be found on this system."
fi

say "  installing Python 3.13"
"$UV" python install 3.13 >/dev/null 2>&1 || die "uv could not install Python 3.13"

# -- release -----------------------------------------------------------------

say "  reading the release manifest"
curl -fsSL "$BASE/manifest.json" -o "$WORK/manifest.json" || die "could not read $BASE/manifest.json"
WHEEL="$(sed -n 's/.*"wheel"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK/manifest.json")"
RELEASE="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK/manifest.json")"
[ -n "$WHEEL" ] && [ -n "$RELEASE" ] || die "the release manifest is malformed."
say "  version $RELEASE"

TARGET="$VERSIONS/$RELEASE"
curl -fsSL "$BASE/$WHEEL" -o "$WORK/$WHEEL" || die "could not download $WHEEL"

say "  installing the runtime"
rm -rf "$TARGET"
mkdir -p "$TARGET"
"$UV" venv --python 3.13 "$TARGET" >/dev/null 2>&1 || die "could not create the runtime environment"
"$UV" pip install --python "$TARGET/bin/python" "$WORK/$WHEEL" >/dev/null || die "could not install $WHEEL"

# -- postgres ----------------------------------------------------------------

if [ -x "$PG_ROOT/bin/initdb" ]; then
  say "  PostgreSQL already installed"
else
  say "  downloading PostgreSQL"
  ASSET="postgres-16-$TAG.txz"
  curl -fsSL "$BASE/$ASSET" -o "$WORK/$ASSET" || die "could not download $ASSET"
  curl -fsSL "$BASE/SHA256SUMS" -o "$WORK/SHA256SUMS" || die "could not download SHA256SUMS"
  expected="$(grep " $ASSET\$" "$WORK/SHA256SUMS" | cut -d' ' -f1)"
  [ -n "$expected" ] || die "SHA256SUMS does not list $ASSET"
  actual="$(sha256 "$WORK/$ASSET")"
  if [ "$expected" != "$actual" ]; then
    die "$ASSET failed its checksum.
  expected $expected
  actual   $actual
Nothing was installed."
  fi
  rm -rf "$PG_ROOT"
  mkdir -p "$PG_ROOT"
  tar -xJf "$WORK/$ASSET" -C "$PG_ROOT" || die "could not unpack $ASSET"
fi

# -- command -----------------------------------------------------------------

ln -sfn "$TARGET" "$CURRENT"
mkdir -p "$BIN_DIR"
cat > "$COMMAND" <<EOF
#!/bin/sh
# Written by the Orin installer. Points at the current version, so an update
# repoints one symlink instead of rewriting whatever is on your PATH.
exec "$CURRENT/bin/orin" "\$@"
EOF
chmod +x "$COMMAND"

if [ "$MODIFY_PATH" -eq 1 ] && ! printf '%s' ":$PATH:" | grep -q ":$BIN_DIR:"; then
  for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile"; do
    [ -f "$rc" ] || continue
    grep -q "# >>> orin >>>" "$rc" && continue
    printf '\n# >>> orin >>>\nexport PATH="%s:$PATH"\n# <<< orin <<<\n' "$BIN_DIR" >> "$rc"
    say "  added $BIN_DIR to PATH in $rc"
  done
fi

say ""
say "Orin $RELEASE installed."
say "  command  $COMMAND"
say "  runtime  $CURRENT"
say ""
if printf '%s' ":$PATH:" | grep -q ":$BIN_DIR:"; then
  say "Run:"
  say "  orin"
else
  say "Open a new terminal and run:"
  say "  orin"
  say ""
  say "Or run it now with:"
  say "  $COMMAND"
fi
say ""
```

- [ ] **Step 2: Check it for portability mistakes**

```bash
shellcheck -s sh install.sh
```

Expected: no errors. If `shellcheck` is unavailable, install it (`brew install shellcheck` / `apt install shellcheck`) — a shell installer that runs on three platforms is exactly the code worth linting. Warnings about `SC2039` would indicate a bashism that must be removed.

- [ ] **Step 3: Verify the failure paths without a release**

```bash
sh install.sh --version 0.0.0-nonexistent
```

Expected: fails at `could not read .../manifest.json`, and creates nothing:

```bash
ls ~/.local/share/orin/app/versions 2>&1
```

Expected: no such directory, or unchanged from before.

- [ ] **Step 4: Commit**

```bash
git add install.sh
git commit -m "feat(install): install Orin on macOS and Linux"
```

---

### Task 3: The Windows installer

**Files:**
- Create: `install.ps1`

**Interfaces:**
- Same contract as `install.sh`, with `-Version`, `-NoModifyPath`, `-Uninstall`.

- [ ] **Step 1: Write the script**

Create `install.ps1` at the repository root:

```powershell
<#
.SYNOPSIS
Install Orin on Windows.

.DESCRIPTION
    irm https://github.com/carlos-edu2367/orin/releases/latest/download/install.ps1 | iex

No administrator, no system-wide changes: everything lands under your user
profile and only your user PATH is modified. Set ORIN_INSTALL_BASE to install
from somewhere other than the latest GitHub release.

.EXAMPLE
.\install.ps1 -Version 0.1.0

.EXAMPLE
.\install.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$NoModifyPath,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$base = if ($env:ORIN_INSTALL_BASE) { $env:ORIN_INSTALL_BASE } else { "https://github.com/carlos-edu2367/orin/releases/latest/download" }
if ($Version) { $base = $base -replace "/releases/latest/download", "/releases/download/v$Version" }

$local     = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME "AppData\Local" }
$appRoot   = Join-Path $local "Programs\Orin"
$versions  = Join-Path $appRoot "versions"
$current   = Join-Path $appRoot "current"
$binDir    = Join-Path $local "Orin\bin"
$command   = Join-Path $binDir "orin.cmd"
$pgRoot    = Join-Path $local "Orin\runtime\postgres\16"

function Update-UserPath([string]$directory, [switch]$Remove) {
    # The user PATH only; never the machine PATH. Installing Orin must not
    # require an administrator and must not affect anyone else on the machine.
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @($current -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ne $directory.TrimEnd('\') })
    if (-not $Remove) { $entries += $directory }
    [Environment]::SetEnvironmentVariable("Path", ($entries -join ';'), "User")
}

if ($Uninstall) {
    if (Test-Path $command) { Remove-Item $command -Force }
    if (Test-Path $current) { (Get-Item $current).Delete() }
    if (Test-Path $appRoot) { Remove-Item $appRoot -Recurse -Force }
    Update-UserPath $binDir -Remove
    Write-Host ""
    Write-Host "Orin removed. Your data was not touched:" -ForegroundColor Yellow
    Write-Host "  data      $local\Orin\data"
    Write-Host "  config    $env:APPDATA\Orin\config"
    Write-Host "  logs      $local\Orin\logs"
    Write-Host "  database  $local\Orin\data\postgres"
    Write-Host ""
    Write-Host "Delete them yourself if you want them gone."
    return
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Orin has no build for 32-bit Windows."
}

$work = Join-Path ([IO.Path]::GetTempPath()) ("orin-install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $work | Out-Null
try {
    Write-Host ""
    Write-Host "Installing Orin for windows-amd64" -ForegroundColor Cyan

    # -- uv ------------------------------------------------------------
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) {
        Write-Host "  installing uv"
        $env:UV_NO_MODIFY_PATH = "1"
        & ([scriptblock]::Create((Invoke-RestMethod https://astral.sh/uv/install.ps1))) | Out-Null
        $uv = Join-Path $local "Programs\uv\uv.exe"
        if (-not (Test-Path $uv)) { $uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe" }
        if (-not (Test-Path $uv)) { throw "uv installed but could not be found." }
    }

    Write-Host "  installing Python 3.13"
    & $uv python install 3.13 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "uv could not install Python 3.13" }

    # -- release -------------------------------------------------------
    Write-Host "  reading the release manifest"
    $manifest = Invoke-RestMethod "$base/manifest.json"
    $wheel   = $manifest.wheel
    $release = $manifest.version
    if (-not $wheel -or -not $release) { throw "the release manifest is malformed." }
    Write-Host "  version $release"

    $wheelPath = Join-Path $work $wheel
    Invoke-WebRequest "$base/$wheel" -OutFile $wheelPath -UseBasicParsing

    Write-Host "  installing the runtime"
    $target = Join-Path $versions $release
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    & $uv venv --python 3.13 $target | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "could not create the runtime environment" }
    & $uv pip install --python (Join-Path $target "Scripts\python.exe") $wheelPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "could not install $wheel" }

    # -- postgres ------------------------------------------------------
    if (Test-Path (Join-Path $pgRoot "bin\initdb.exe")) {
        Write-Host "  PostgreSQL already installed"
    } else {
        Write-Host "  downloading PostgreSQL"
        $asset = "postgres-16-windows-amd64.txz"
        $assetPath = Join-Path $work $asset
        Invoke-WebRequest "$base/$asset" -OutFile $assetPath -UseBasicParsing
        $sums = (Invoke-WebRequest "$base/SHA256SUMS" -UseBasicParsing).Content
        $expected = ($sums -split "`n" | Where-Object { $_ -match [regex]::Escape($asset) } | Select-Object -First 1) -split '\s+' | Select-Object -First 1
        if (-not $expected) { throw "SHA256SUMS does not list $asset" }
        $actual = (Get-FileHash $assetPath -Algorithm SHA256).Hash.ToLower()
        if ($expected.ToLower() -ne $actual) {
            throw "$asset failed its checksum.`n  expected $expected`n  actual   $actual`nNothing was installed."
        }
        if (Test-Path $pgRoot) { Remove-Item $pgRoot -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $pgRoot | Out-Null
        # tar.exe has shipped with Windows since 1803 and handles .txz.
        & tar.exe -xJf $assetPath -C $pgRoot
        if ($LASTEXITCODE -ne 0) { throw "could not unpack $asset" }
    }

    # -- command -------------------------------------------------------
    if (Test-Path $current) { (Get-Item $current).Delete() }
    New-Item -ItemType Junction -Path $current -Target $target | Out-Null

    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    # A shim, not a copy: the command always resolves through `current`, so an
    # update repoints one junction instead of rewriting what is on PATH.
    @"
@echo off
"$current\Scripts\orin.exe" %*
"@ | Set-Content -Path $command -Encoding ascii

    if (-not $NoModifyPath) {
        $onPath = ([Environment]::GetEnvironmentVariable("Path", "User") -split ';') |
            Where-Object { $_.TrimEnd('\') -eq $binDir.TrimEnd('\') }
        if (-not $onPath) { Update-UserPath $binDir }
    }

    Write-Host ""
    Write-Host "Orin $release installed." -ForegroundColor Green
    Write-Host "  command   $command"
    Write-Host "  runtime   $current"
    Write-Host ""
    Write-Host "Open a new terminal and run:" -ForegroundColor Cyan
    Write-Host "  orin"
    Write-Host ""
}
finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
```

- [ ] **Step 2: Verify the failure path leaves nothing behind**

```powershell
powershell -NoProfile -File .\install.ps1 -Version 0.0.0-nonexistent
```

Expected: it throws on the manifest request. Then confirm nothing was created:

```powershell
Test-Path "$env:LOCALAPPDATA\Programs\Orin\versions\0.0.0-nonexistent"
```

Expected: `False`.

- [ ] **Step 3: Mark the development installer as such**

In `scripts/install-orin.ps1`, replace the `.DESCRIPTION` paragraph about the "future public installer" with:

```
This is the development-profile installer: it registers a shim pointing at the
runtime in this checkout, so a `git pull` is reflected in `orin` immediately.

The public installer is `install.ps1` at the repository root, published with
every release. It writes the same shim to the same directory; the only
difference is that its runtime comes from a downloaded release rather than from
this checkout.
```

- [ ] **Step 4: Commit**

```bash
git add install.ps1 scripts/install-orin.ps1
git commit -m "feat(install): install Orin on Windows"
```

---

### Task 4: `orin update` and `orin uninstall`

Installing from outside and updating from inside must not drift. `update` therefore re-runs the published installer rather than reimplementing it — one code path, tested once. `uninstall` is Python, because it has to remove a PATH entry the shell cannot remove for itself.

**Files:**
- Create: `src/agentos/launcher/maintenance.py`, `tests/unit/launcher/test_maintenance.py`
- Modify: `src/agentos/launcher/cli.py:70-86`, `:244-259`

**Interfaces:**
- Consumes: `InstallationLayout` (Task 1), `OrinPaths`, `RuntimeProfile`, `Console`, `running_instance`.
- Produces:
  - `command_update(paths, profile, console) -> int`
  - `command_uninstall(paths, profile, console, *, assume_yes: bool = False) -> int`
  - `remove_from_user_path(directory: Path) -> bool`
  - `DEFAULT_INSTALL_BASE: str`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/launcher/test_maintenance.py`:

```python
from __future__ import annotations

from pathlib import Path

from agentos.installation.layout import InstallationLayout
from agentos.installation.paths import OrinPaths
from agentos.installation.profile import RuntimeProfile
from agentos.launcher import maintenance
from agentos.launcher.ui import Console


class Recorder(Console):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def line(self, text: str = "") -> None:
        self.lines.append(text)

    def error(self, text: str) -> None:
        self.lines.append(f"error: {text}")

    def detail(self, text: str) -> None:
        self.lines.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()


def _layout(tmp_path: Path) -> InstallationLayout:
    root = tmp_path / "app"
    layout = InstallationLayout(root, root / "versions", root / "current", tmp_path / "bin", tmp_path / "bin" / "orin")
    (layout.versions / "0.1.0").mkdir(parents=True)
    layout.bin_dir.mkdir(parents=True)
    layout.command.write_text("shim", encoding="utf-8")
    layout.current.mkdir()
    return layout


def test_updating_a_development_checkout_is_refused(tmp_path: Path, monkeypatch) -> None:
    console = Recorder()
    profile = RuntimeProfile("development", tmp_path, "0.1.0", tmp_path)

    assert maintenance.command_update(_paths(tmp_path), profile, console) == 2
    assert "git pull" in console.text


def test_updating_while_orin_is_running_is_refused(tmp_path: Path, monkeypatch) -> None:
    console = Recorder()
    profile = RuntimeProfile("installed", tmp_path, "0.1.0", None)
    monkeypatch.setattr(maintenance, "running_instance", lambda paths: object())

    assert maintenance.command_update(_paths(tmp_path), profile, console) == 1
    assert "orin stop" in console.text


def test_uninstalling_removes_the_command_and_the_versions(tmp_path: Path, monkeypatch) -> None:
    console = Recorder()
    layout = _layout(tmp_path)
    monkeypatch.setattr(maintenance.InstallationLayout, "resolve", classmethod(lambda cls: layout))
    monkeypatch.setattr(maintenance, "remove_from_user_path", lambda directory: True)
    monkeypatch.setattr(maintenance, "running_instance", lambda paths: None)
    profile = RuntimeProfile("installed", tmp_path, "0.1.0", None)

    assert maintenance.command_uninstall(_paths(tmp_path), profile, console, assume_yes=True) == 0
    assert not layout.command.exists()
    assert not layout.root.exists()


def test_uninstalling_never_touches_data_config_or_logs(tmp_path: Path, monkeypatch) -> None:
    console = Recorder()
    layout = _layout(tmp_path)
    paths = _paths(tmp_path)
    (paths.data / "keep.txt").write_text("a conversation", encoding="utf-8")
    monkeypatch.setattr(maintenance.InstallationLayout, "resolve", classmethod(lambda cls: layout))
    monkeypatch.setattr(maintenance, "remove_from_user_path", lambda directory: True)
    monkeypatch.setattr(maintenance, "running_instance", lambda paths: None)
    profile = RuntimeProfile("installed", tmp_path, "0.1.0", None)

    maintenance.command_uninstall(paths, profile, console, assume_yes=True)

    assert (paths.data / "keep.txt").read_text(encoding="utf-8") == "a conversation"
    assert paths.config.exists()
    assert paths.logs.exists()
    # And it has to say so, with the paths, or the user cannot finish the job.
    assert str(paths.data) in console.text
    assert str(paths.config) in console.text
```

- [ ] **Step 2: Run them and verify they fail**

```bash
python -m pytest tests/unit/launcher/test_maintenance.py -q
```

Expected: `ModuleNotFoundError: No module named 'agentos.launcher.maintenance'`.

- [ ] **Step 3: Write the module**

Create `src/agentos/launcher/maintenance.py`:

```python
"""Updating and removing an installation.

``orin update`` deliberately re-runs the published installer instead of
reimplementing it here. There is then exactly one procedure that knows how to
put Orin on a machine, and the install smoke matrix exercises it twice: once as
a first install and once as an update.

``orin uninstall`` is the opposite: it must remove a PATH entry, which no
installer can do from inside the shell that inherited it, so it is written here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from agentos.installation import InstallationLayout, OrinPaths, RuntimeProfile

from .state import running_instance
from .ui import Console

DEFAULT_INSTALL_BASE = "https://github.com/carlos-edu2367/orin/releases/latest/download"

_PATH_MARKERS = ("# >>> orin >>>", "# <<< orin <<<")


def _install_base() -> str:
    return os.getenv("ORIN_INSTALL_BASE", DEFAULT_INSTALL_BASE).rstrip("/")


def command_update(paths: OrinPaths, profile: RuntimeProfile, console: Console) -> int:
    """Install the latest release beside this one and repoint the command."""
    if profile.is_development:
        console.error(
            "This is a development checkout, not an installation.\n"
            "  Update it with 'git pull', then rebuild the interface if it changed."
        )
        return 2
    if running_instance(paths) is not None:
        console.error("Orin is running. Stop it first:\n  orin stop")
        return 1

    base = _install_base()
    console.line("")
    console.line(f"  Updating Orin from {base}")
    console.line("")
    try:
        if os.name == "nt":
            completed = subprocess.run(  # noqa: S603 - fixed interpreter, URL from the environment or the default
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                    f"$env:ORIN_INSTALL_BASE='{base}'; irm {base}/install.ps1 | iex",
                ],
                check=False,
            )
        else:
            completed = subprocess.run(  # noqa: S603 - fixed interpreter, URL from the environment or the default
                ["/bin/sh", "-c", f"curl -fsSL {base}/install.sh | ORIN_INSTALL_BASE='{base}' sh"],
                check=False,
            )
    except OSError as error:
        console.error(f"Could not run the installer: {error}")
        return 1
    if completed.returncode != 0:
        console.error("The update did not complete. Your existing installation was left in place.")
        return completed.returncode
    console.line("  Orin is up to date.")
    console.line("")
    return 0


def command_uninstall(paths: OrinPaths, profile: RuntimeProfile, console: Console, *, assume_yes: bool = False) -> int:
    """Remove the command and every installed version. Never the user's data."""
    if profile.is_development:
        console.error(
            "This is a development checkout, not an installation.\n"
            "  Remove the command with: .\\scripts\\install-orin.ps1 -Uninstall"
        )
        return 2
    if running_instance(paths) is not None:
        console.error("Orin is running. Stop it first:\n  orin stop")
        return 1

    layout = InstallationLayout.resolve()
    console.line("")
    console.line("  This removes the orin command and the installed runtime.")
    console.line("  It does not remove:")
    console.line(f"    data      {paths.data}")
    console.line(f"    config    {paths.config}")
    console.line(f"    logs      {paths.logs}")
    console.line("")
    if not assume_yes:
        try:
            answer = input("  Remove Orin? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in {"y", "yes"}:
            console.line("  Nothing was removed.")
            console.line("")
            return 0

    for target in (layout.command, layout.current):
        try:
            if target.is_symlink() or target.exists():
                target.unlink()
        except (OSError, IsADirectoryError):
            shutil.rmtree(target, ignore_errors=True)
    shutil.rmtree(layout.root, ignore_errors=True)
    remove_from_user_path(layout.bin_dir)

    console.line("  Orin removed.")
    console.line("")
    console.line("  Delete the directories above yourself if you want them gone.")
    console.line("")
    return 0


def remove_from_user_path(directory: Path) -> bool:
    """Take the installer's directory back off PATH. ``True`` if anything changed."""
    if os.name == "nt":
        return _remove_from_windows_path(directory)
    return _remove_from_shell_profiles()


def _remove_from_windows_path(directory: Path) -> bool:
    try:
        import winreg
    except ImportError:  # pragma: no cover - Windows only
        return False
    wanted = str(directory).rstrip("\\").lower()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            current, kind = winreg.QueryValueEx(key, "Path")
            entries = [entry for entry in str(current).split(";") if entry and entry.rstrip("\\").lower() != wanted]
            updated = ";".join(entries)
            if updated == current:
                return False
            winreg.SetValueEx(key, "Path", 0, kind, updated)
    except OSError:
        return False
    return True


def _remove_from_shell_profiles() -> bool:
    changed = False
    start, end = _PATH_MARKERS
    for name in (".zshrc", ".bashrc", ".profile"):
        rc = Path.home() / name
        try:
            lines = rc.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError:
            continue
        kept, skipping = [], False
        for line in lines:
            if start in line:
                skipping = True
                changed = True
                continue
            if end in line:
                skipping = False
                continue
            if not skipping:
                kept.append(line)
        if changed:
            try:
                rc.write_text("".join(kept), encoding="utf-8")
            except OSError:
                pass
    return changed


__all__ = ["DEFAULT_INSTALL_BASE", "command_uninstall", "command_update", "remove_from_user_path"]
```

Note: `sys` is imported but may be unused — remove the import if `ruff` flags it.

- [ ] **Step 4: Add the verbs to the CLI**

In `src/agentos/launcher/cli.py`, after the `status` subcommand registration (line 74):

```python
    commands.add_parser("update", help="install the latest Orin release")
    uninstall = commands.add_parser("uninstall", help="remove the orin command and runtime, keeping your data")
    uninstall.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
```

And in `main`, after the `logs` branch (line 253):

```python
        if command == "update":
            from .maintenance import command_update

            return command_update(paths, profile, console)
        if command == "uninstall":
            from .maintenance import command_uninstall

            return command_uninstall(paths, profile, console, assume_yes=arguments.yes)
```

Add both to the parser epilog's examples list:

```python
            "  orin update             install the latest release\n"
```

- [ ] **Step 5: Run the tests**

```bash
python -m pytest tests/unit/launcher -q
```

Expected: all pass.

```bash
orin --help
```

Expected: `update` and `uninstall` appear in the command list.

```bash
orin update
```

Expected, from a checkout: `This is a development checkout, not an installation.` and exit code 2.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/launcher/maintenance.py src/agentos/launcher/cli.py tests/unit/launcher/test_maintenance.py
git commit -m "feat(launcher): add orin update and orin uninstall"
```

---

### Task 5: Prove the installers on all three operating systems

This is the task that earns the claim. Everything before it is untested belief.

**Files:**
- Create: `.github/workflows/install-smoke.yml`
- Modify: `.github/workflows/release.yml` (call the smoke job)

**Interfaces:**
- Consumes: the release artifacts from part two, and the installers from Tasks 2 and 3.
- Produces: a required check named `smoke` on every release.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/install-smoke.yml`:

```yaml
name: install-smoke

on:
  workflow_call:
    inputs:
      artifact:
        description: "Name of the release artifact to install from"
        required: true
        type: string
  workflow_dispatch:
    inputs:
      artifact:
        description: "Name of the release artifact to install from"
        required: true

jobs:
  smoke:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/download-artifact@v4
        with:
          name: ${{ inputs.artifact }}
          path: release

      - name: Serve the release over http
        # The installers fetch by URL. Pointing ORIN_INSTALL_BASE at a local
        # server tests the real download path without needing a published
        # release, and without the installers knowing they are being tested.
        shell: bash
        run: |
          cd release
          python -m http.server 8099 &
          for _ in $(seq 1 30); do
            curl -fsS http://127.0.0.1:8099/manifest.json >/dev/null && break
            sleep 1
          done
          curl -fsS http://127.0.0.1:8099/manifest.json

      - name: Install (POSIX)
        if: runner.os != 'Windows'
        env:
          ORIN_INSTALL_BASE: http://127.0.0.1:8099
        run: sh ./install.sh

      - name: Install (Windows)
        if: runner.os == 'Windows'
        env:
          ORIN_INSTALL_BASE: http://127.0.0.1:8099
        shell: powershell
        run: .\install.ps1

      - name: Start Orin and wait for it to answer
        shell: bash
        run: |
          set -euo pipefail
          if [ "${RUNNER_OS}" = "Windows" ]; then
            ORIN="$LOCALAPPDATA/Orin/bin/orin.cmd"
          else
            ORIN="$HOME/.local/bin/orin"
          fi
          echo "ORIN=$ORIN" >> "$GITHUB_ENV"
          "$ORIN" --version
          # nohup and a detached stdio, or the runner reaps it when the step
          # ends and the next step tests a process that is already gone.
          nohup "$ORIN" --no-browser -v > "$RUNNER_TEMP/orin-start.log" 2>&1 < /dev/null &
          disown || true
          for _ in $(seq 1 180); do
            if curl -fsS http://127.0.0.1:8000/readyz >/dev/null 2>&1; then
              echo "ready"
              exit 0
            fi
            sleep 2
          done
          echo "::error::Orin never became ready"
          cat "$RUNNER_TEMP/orin-start.log" || true
          "$ORIN" logs -n 100 || true
          "$ORIN" logs --service backend -n 100 || true
          exit 1

      - name: Status reports running
        shell: bash
        run: "$ORIN" status

      - name: The interface is actually served
        shell: bash
        run: curl -fsS http://127.0.0.1:8000/ | grep -q 'id="root"'

      - name: Stop
        shell: bash
        run: |
          "$ORIN" stop
          if "$ORIN" status; then
            echo "::error::status reported running after stop"
            exit 1
          fi
          echo "status exited non-zero after stop, as it must"

      - name: No database process was left behind
        if: runner.os != 'Windows'
        run: |
          if pgrep -f "postgres.*orin" >/dev/null 2>&1; then
            echo "::error::a postgres process survived orin stop"
            pgrep -af "postgres.*orin"
            exit 1
          fi

      - name: Update over the top of the installation
        shell: bash
        env:
          ORIN_INSTALL_BASE: http://127.0.0.1:8099
        run: |
          "$ORIN" update
          "$ORIN" --version

      - name: Uninstall keeps the data
        shell: bash
        run: |
          set -euo pipefail
          if [ "${RUNNER_OS}" = "Windows" ]; then DATA="$LOCALAPPDATA/Orin/data"; else DATA="$HOME/.local/share/orin/data"; fi
          test -d "$DATA" || { echo "::error::no data directory to preserve"; exit 1; }
          "$ORIN" uninstall --yes
          test -d "$DATA" || { echo "::error::uninstall deleted the user's data"; exit 1; }
          test ! -f "$ORIN" || { echo "::error::the command survived uninstall"; exit 1; }
          echo "uninstall removed the command and kept the data"
```

- [ ] **Step 2: Call it from the release workflow**

In `.github/workflows/release.yml`, add a job after `build` and make publishing depend on it. Move the two `gh release create` steps out of `build` into a new `publish` job:

```yaml
  smoke:
    needs: build
    uses: ./.github/workflows/install-smoke.yml
    with:
      artifact: release-${{ needs.build.outputs.version }}

  publish:
    needs: [build, smoke]
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: release-${{ needs.build.outputs.version }}
          path: release
      - name: Publish the release
        if: github.event_name == 'push'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh release create "${GITHUB_REF_NAME}" release/* --generate-notes --title "Orin ${GITHUB_REF_NAME}" --repo "${GITHUB_REPOSITORY}"
      - name: Publish a draft (manual run)
        if: github.event_name == 'workflow_dispatch'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh release create "${{ inputs.tag }}" release/* --draft --generate-notes --title "Orin ${{ inputs.tag }} (draft)" --repo "${GITHUB_REPOSITORY}"
```

A release that cannot be installed is not published. That ordering is the point.

- [ ] **Step 3: Run the whole thing end to end as a dry run**

```bash
git add .github/workflows && git commit -m "ci: prove the installers on Windows, macOS and Linux" && git push
```

```bash
gh workflow run release.yml -f tag=v0.1.0
```

```bash
gh run watch
```

Expected: `build` green, then three `smoke` jobs green, then a draft release. **Expect this to fail the first time.** The three most likely causes, in order:

1. The `install.sh` PATH block runs but the smoke job's `$ORIN` uses the absolute path, so a PATH problem hides — that is deliberate; the shim is what is being tested, not the shell's rc file.
2. `initdb` timing out on the Windows runner behind Defender. If so, raise `INITDB_TIMEOUT_SECONDS` and re-run; do not paper over it with a retry loop.
3. `tar.exe` failing on `.txz` on the Windows runner. If so, unpack with `& $uv tool run --from ... ` is not an answer — use `7z` if present, otherwise switch the Windows asset to `.zip` in `scripts/mirror-postgres.py` and update `asset_name` in both `postgres_binaries.py` and the script together.

Fix, push, re-run until all three are green.

- [ ] **Step 4: Delete the draft and commit the fixes**

```bash
gh release delete v0.1.0 --yes
```

```bash
git add -A && git commit -m "ci: fix the install smoke matrix" && git push
```

---

### Task 6: Document it, then cut the first release

**Files:**
- Create: `docs/INSTALL.md`, `docs/adr/0001-remove-redis.md`, `docs/adr/0002-embed-postgres.md`
- Modify: `README.md`, `docs/LAUNCHER.md`

**Interfaces:**
- Consumes: everything.
- Produces: the public instructions.

- [ ] **Step 1: Write `docs/INSTALL.md`**

```markdown
# Installing Orin

## Windows

```powershell
irm https://github.com/carlos-edu2367/orin/releases/latest/download/install.ps1 | iex
```

## macOS and Linux

```bash
curl -fsSL https://github.com/carlos-edu2367/orin/releases/latest/download/install.sh | sh
```

Then open a new terminal and run `orin`.

Nothing is installed system-wide and nothing asks for administrator. The
installer adds one directory to your user PATH and writes everything else under
your home directory.

## What gets installed

| | Windows | macOS / Linux |
| --- | --- | --- |
| Versions | `%LOCALAPPDATA%\Programs\Orin\versions\<version>` | `~/.local/share/orin/app/versions/<version>` |
| Pointer | `%LOCALAPPDATA%\Programs\Orin\current` | `~/.local/share/orin/app/current` |
| Command | `%LOCALAPPDATA%\Orin\bin\orin.cmd` | `~/.local/bin/orin` |
| PostgreSQL | `%LOCALAPPDATA%\Orin\runtime\postgres\16` | `~/.local/share/orin/runtime/postgres/16` |
| Config | `%APPDATA%\Orin\config` | `~/.config/orin` |
| Data, logs | `%LOCALAPPDATA%\Orin\{data,logs}` | `~/.local/share/orin/{data,logs}` |

An update installs a new version beside the current one and moves the pointer.
Your data is in neither, which is what makes both updating and rolling back
safe.

## Options

```bash
sh install.sh --version 0.2.0      # a specific release
sh install.sh --no-modify-path     # do not touch your shell profile
sh install.sh --uninstall
```

```powershell
.\install.ps1 -Version 0.2.0
.\install.ps1 -NoModifyPath
.\install.ps1 -Uninstall
```

## Updating and removing

```bash
orin update
```

```bash
orin uninstall
```

`orin uninstall` removes the command and the installed runtime. It never
removes your conversations, configuration or logs, and prints where they are so
you can delete them deliberately.

## Using your own PostgreSQL

Orin runs its own database on loopback. Set `DATABASE_URL` in your configuration
file to use one you manage instead, and Orin will start nothing:

```
DATABASE_URL=postgresql+psycopg://user@host:5432/orin
```

## Installing from somewhere else

Both installers read `ORIN_INSTALL_BASE`, which defaults to the latest GitHub
release. Everything is fetched from under it: `manifest.json`, the wheel it
names, `postgres-16-<platform>.txz`, and `SHA256SUMS`.

### Serving the installers from a domain

When a domain is chosen, nothing in the scripts changes. Two steps:

1. Publish `install.ps1` and `install.sh` at the domain root, or redirect
   `/install.ps1` and `/install.sh` to the copies attached to the latest
   release. Serving the files directly is preferable: `irm | iex` and
   `curl | sh` both follow redirects, but a directly served script is what
   users can read before running.
2. Change `DEFAULT_INSTALL_BASE` in `src/agentos/launcher/maintenance.py` and
   the `BASE`/`$base` defaults in the two installers to the new URL, so
   `orin update` follows the domain too.

Until then the GitHub URLs above are the published ones.
```

- [ ] **Step 2: Write the two decision records**

Follow whatever format `docs/adr/` already uses — check with `ls docs/adr` and read one. If the directory is empty, use this shape for `docs/adr/0001-remove-redis.md`:

```markdown
# 1. The queue is a table, not a broker

**Status:** accepted, 2026-08-11

## Context

Orin needed Redis for one thing: arq moved a `turn_id` from the publisher to the
chat worker. The durable record of that turn already lived in
`conversation_dispatches` in PostgreSQL, and the claim that stops two workers
running the same turn was already a conditional `UPDATE` in that table.

Redis was also the reason Orin could not be installed on Windows without Docker:
there is no official Redis build for Windows.

## Decision

Remove Redis and arq. The worker polls `conversation_dispatches` and claims with
the `UPDATE` that already existed. The publisher keeps the state transition and
the recovery sweep.

## Consequences

- One datastore to install, start, stop and back up.
- Dispatch latency is bounded by the poll interval (250 ms) rather than a
  broker push. The publisher already polled at 500 ms, so this is faster than
  what it replaced.
- Horizontal scaling beyond one machine would need revisiting. Orin is a
  local-first single-user workspace; that is not a trade being made blindly.
```

And `docs/adr/0002-embed-postgres.md`, recording: Docker Desktop is not an assumption a consumer install can make; the `Services` step was always the seam; binaries are pinned to major 16, mirrored as our own release assets with checksums pinned in the package; `DATABASE_URL` opts out; the consequence is that a future major upgrade needs `pg_upgrade` or dump/restore.

- [ ] **Step 3: Rewrite the README opening**

Replace `## Requirements` and `## Start it` in `README.md`:

```markdown
## Install

```powershell
irm https://github.com/carlos-edu2367/orin/releases/latest/download/install.ps1 | iex
```

```bash
curl -fsSL https://github.com/carlos-edu2367/orin/releases/latest/download/install.sh | sh
```

Open a new terminal and run:

```
orin
```

That is the whole thing. Orin brings its own PostgreSQL, prepares it on the
first run, starts the backend and the workers, waits until the interface
actually answers, and opens it in your browser. `Ctrl+C` stops everything it
started.

The only thing you need to supply is an API key for a provider (OpenRouter,
OpenAI, or Anthropic), which you paste into **Settings → Providers** after Orin
is running.

See [docs/INSTALL.md](docs/INSTALL.md) for what gets installed where, updating,
uninstalling, and using your own database.

## Building from source

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
.\scripts\install-orin.ps1
```

This registers `orin` against the runtime in this checkout, so a `git pull` is
reflected immediately.
```

- [ ] **Step 4: Point the launcher document at the new one**

In `docs/LAUNCHER.md`, replace the `## Installing the command` section's first paragraph with a link to `docs/INSTALL.md` for installed use, keeping the `scripts/install-orin.ps1` instructions clearly labelled as the development path.

- [ ] **Step 5: Commit the documentation**

```bash
git add README.md docs/INSTALL.md docs/LAUNCHER.md docs/adr
git commit -m "docs: publish the install, update and uninstall instructions"
git push
```

- [ ] **Step 6: Cut the first release**

Everything is now proven by CI. Tag it:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

```bash
gh run watch
```

Expected: `build` → three `smoke` jobs → `publish`, all green, and a published release.

- [ ] **Step 7: Install it the way a stranger would**

On a machine that has never had Orin — ideally not the development machine — run the published one-liner, then `orin`, then send one message and confirm the agent answers. Nothing before this step proves the product; it proves the pipeline.

```bash
curl -fsSL https://github.com/carlos-edu2367/orin/releases/latest/download/install.sh | sh
```

---

## Definition of done

- [ ] `install.sh` passes `shellcheck -s sh`.
- [ ] The smoke matrix is green on `ubuntu-latest`, `macos-latest` and `windows-latest`, covering install → start → ready → status → stop → update → uninstall.
- [ ] Uninstall is proven by CI to leave the data directory intact.
- [ ] A release is not published unless the smoke matrix passed.
- [ ] `orin update` works from an installed version and is refused from a checkout.
- [ ] `docs/INSTALL.md` documents the domain cutover as two steps with no script rewrite.
- [ ] The published one-liner has been run on a machine that never had Orin.
