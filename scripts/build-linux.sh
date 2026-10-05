#!/usr/bin/env bash
# Linux counterpart of scripts/build-windows.ps1. Freezes the Python runtime
# with PyInstaller, packages the Electron shell around it with
# electron-builder, and leaves an unpacked directory tree ready for
# package-release.sh to tar.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "Create the development virtual environment (uv sync) before building a release." >&2
  exit 1
fi

cd "$ROOT"

npm ci --prefix frontend
npm run build --prefix frontend

"$PYTHON" -m PyInstaller packaging/orin.spec --noconfirm --clean

frozen_runtime="$ROOT/dist/runtime"
# Chromium is an on-demand download now; the release must carry the driver that
# fetches it, and must not carry the browser itself.
if ! find "$frozen_runtime" -type d -path '*playwright/driver' | grep -q .; then
  echo "Frozen runtime was built without the Playwright driver." >&2
  exit 1
fi
if find "$frozen_runtime" -type f -name chrome -path '*chrome-linux*' | grep -q .; then
  echo "Frozen runtime unexpectedly bundles Chromium; it must stay an optional download." >&2
  exit 1
fi

if [ "${SKIP_TESTS:-0}" != "1" ]; then
  "$PYTHON" -m pytest -q tests/unit
fi

cd desktop
npm ci
npm run build:dir:linux
cd "$ROOT"

scripts/package-release.sh
