#!/usr/bin/env bash
# Linux counterpart of scripts/build-windows.ps1. Freezes the Python runtime
# with PyInstaller, packages the Electron shell around it with
# electron-builder, and leaves an unpacked directory tree ready for
# package-release.sh to tar.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
BROWSER_ROOT="$ROOT/build/playwright"

if [ ! -x "$PYTHON" ]; then
  echo "Create the development virtual environment (uv sync) before building a release." >&2
  exit 1
fi

cd "$ROOT"

npm ci --prefix frontend
npm run build --prefix frontend

export PLAYWRIGHT_BROWSERS_PATH="$BROWSER_ROOT"
"$PYTHON" -m playwright install chromium
export ORIN_PLAYWRIGHT_BROWSERS_PATH="$BROWSER_ROOT"

"$PYTHON" -m PyInstaller packaging/orin.spec --noconfirm --clean

frozen_runtime="$ROOT/dist/runtime"
chromium=$(find "$frozen_runtime" -type f -name chrome -path '*chrome-linux*' | head -n1)
if [ -z "$chromium" ]; then
  echo "Frozen runtime was built without a Chromium executable." >&2
  exit 1
fi
echo "Bundled Chromium: $chromium"

if [ "${SKIP_TESTS:-0}" != "1" ]; then
  packaging_browser_path="${ORIN_PLAYWRIGHT_BROWSERS_PATH:-}"
  playwright_browser_path="${PLAYWRIGHT_BROWSERS_PATH:-}"
  unset ORIN_PLAYWRIGHT_BROWSERS_PATH PLAYWRIGHT_BROWSERS_PATH
  "$PYTHON" -m pytest -q tests/unit
  [ -n "$packaging_browser_path" ] && export ORIN_PLAYWRIGHT_BROWSERS_PATH="$packaging_browser_path"
  [ -n "$playwright_browser_path" ] && export PLAYWRIGHT_BROWSERS_PATH="$playwright_browser_path"
fi

cd desktop
npm ci
npm run build:dir:linux
cd "$ROOT"

# package-release.sh is created by a later plan task (Task 8); call it only
# if present so this script is independently runnable/verifiable now. Task
# 8's dispatch restores this as an unconditional call once the script exists.
if [ -x scripts/package-release.sh ]; then
  scripts/package-release.sh
fi
