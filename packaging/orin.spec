# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the self-contained Orin launcher.

Build with ``python -m PyInstaller packaging/orin.spec --noconfirm`` after
building ``frontend/dist`` and provisioning Chromium into the supplied browser
directory. The resulting one-directory runtime contains no Python/Node/Docker
requirement on the target machine.
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH).parent
WEB = ROOT / "frontend" / "dist"
BROWSERS = Path(os.environ["ORIN_PLAYWRIGHT_BROWSERS_PATH"])

if not (WEB / "index.html").is_file():
    raise SystemExit("frontend/dist is missing; run npm --prefix frontend run build first")
if not BROWSERS.is_dir():
    raise SystemExit("ORIN_PLAYWRIGHT_BROWSERS_PATH is missing; provision Chromium before packaging")

INSTALLER_SCRIPT = ROOT / ("install.ps1" if os.name == "nt" else "install.sh")

datas = collect_data_files("agentos")
datas += copy_metadata("agentos")
datas += [
    (str(WEB), "web"),
    (str(BROWSERS), "playwright"),
    (str(INSTALLER_SCRIPT), "."),
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
