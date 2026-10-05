#!/usr/bin/env bash
# Linux counterpart of scripts/build-setup.ps1: builds dist/OrinSetup-linux-x64,
# the program install.sh downloads and runs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || { echo "Create the development virtual environment (uv sync) first." >&2; exit 1; }

cd "$ROOT"
"$PYTHON" -m PyInstaller packaging/setup.spec --noconfirm --clean --distpath dist --workpath build/setup
setup="dist/OrinSetup-linux-x64"
[ -x "$setup" ] || { echo "$setup was not produced." >&2; exit 1; }
"$setup" --help >/dev/null
echo "Installer: $setup"
sha256sum "$setup"
