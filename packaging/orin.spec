# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the self-contained Orin launcher.

Build with ``python -m PyInstaller packaging/orin.spec --noconfirm`` after
building ``frontend/dist``. The resulting one-directory runtime contains no
Python/Node/Docker requirement on the target machine.

Chromium is deliberately *not* bundled: it is the largest part of a release and
most people never use the agent's browser. The Playwright driver is bundled
instead, and downloads Chromium on demand into the per-user cache (see
``agentos.browser.engine`` and ``orin browser install``).
"""
import importlib.util
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH).parent
WEB = ROOT / "frontend" / "dist"
PLAYWRIGHT_DRIVER = Path(importlib.util.find_spec("playwright").origin).parent / "driver"

if not (WEB / "index.html").is_file():
    raise SystemExit("frontend/dist is missing; run npm --prefix frontend run build first")
if not (PLAYWRIGHT_DRIVER / "package").is_dir():
    raise SystemExit("The Playwright driver is missing; run uv sync before packaging")

datas = collect_data_files("agentos")
datas += copy_metadata("agentos")
datas += [
    (str(WEB), "web"),
    (str(PLAYWRIGHT_DRIVER), "playwright/driver"),
    (str(ROOT / "src" / "agentos" / "persistence" / "postgres" / "migrations"), "agentos/persistence/postgres/migrations"),
]
binaries = collect_dynamic_libs("pypdfium2")

a = Analysis(
    [str(ROOT / "packaging" / "frozen_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
        # Child services are imported from their string entrypoints only after
        # the frozen launcher re-executes itself.
        "agentos.api.asgi", "agentos.workers.publisher", "agentos.workers.scheduler",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="orin", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="runtime")
