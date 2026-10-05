# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for ``OrinSetup.exe``, the double-click installer.

On Linux it builds ``OrinSetup-linux-x64``, which ``install.sh`` runs in text
mode (and which opens the window instead when tkinter and a display exist).

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
WINDOWS = os.name == "nt"
# Stable asset names: install.ps1 / install.sh download exactly these.
NAME = "OrinSetup" if WINDOWS else "OrinSetup-linux-x64"

a = Analysis(
    [str(ROOT / "packaging" / "setup_entry.py")],
    pathex=[str(ROOT / "src")],
    hiddenimports=["tkinter", "tkinter.filedialog"] + (["winreg"] if WINDOWS else []),
    # Only agentos.installation is imported; keep the heavy runtime out in case
    # a future import drags it in by accident.
    excludes=["httpx", "fastapi", "uvicorn", "sqlalchemy", "pydantic", "playwright", "numpy", "agentos.launcher", "agentos.api"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name=NAME,
    # Windowed on Windows (no console flash); --silent re-attaches to the launching terminal.
    console=not WINDOWS,
    icon=str(ICON) if ICON.is_file() else None,
    upx=False,
)
