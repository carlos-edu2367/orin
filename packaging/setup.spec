# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for ``OrinSetup.exe``, the double-click installer.

A single small executable: the Python installer engine (the same code that backs
``orin update``) and tkinter for its window. It downloads the real release at
install time, so it stays a few megabytes and never goes stale.

Build with ``scripts/build-setup.ps1`` (or ``python -m PyInstaller
packaging/setup.spec --noconfirm --distpath dist --workpath build/setup``).
"""
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
ICON = ROOT / "desktop" / "electron" / "assets" / "orin-logo.ico"

a = Analysis(
    [str(ROOT / "packaging" / "setup_entry.py")],
    pathex=[str(ROOT / "src")],
    hiddenimports=["tkinter", "tkinter.filedialog", "winreg"],
    # Only agentos.installation is imported; keep the heavy runtime out in case
    # a future import drags it in by accident.
    excludes=["httpx", "fastapi", "uvicorn", "sqlalchemy", "pydantic", "playwright", "numpy", "agentos.launcher", "agentos.api"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="OrinSetup",
    console=False,
    icon=str(ICON) if ICON.is_file() else None,
    upx=False,
)
