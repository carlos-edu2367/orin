"""``orin browser``: install or inspect the optional browser engine."""

from __future__ import annotations

import argparse
import os
from time import sleep

from agentos.browser.engine import chromium_installed, engine_installer
from agentos.installation import OrinPaths, RuntimeProfile

from .environment import browser_directory
from .ui import Console


def command_browser(arguments: argparse.Namespace, paths: OrinPaths, profile: RuntimeProfile, console: Console) -> int:
    # The same location the running app resolves, so a terminal install is the
    # one Orin finds (and the other way around).
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browser_directory(paths, profile).resolve()))
    if arguments.browser_command == "status":
        if chromium_installed():
            console.step("O navegador está instalado.")
            return 0
        console.warning("O navegador não está instalado. Rode: orin browser install")
        return 1

    if chromium_installed():
        console.step("O navegador já está instalado.")
        return 0
    console.line("")
    console.line("  Baixando o navegador do agente (cerca de 150 MB)...")
    installer = engine_installer()
    installer.start()
    last = -1
    while (status := installer.status()).state == "installing":
        if status.progress is not None and status.progress != last:
            last = status.progress
            console.line(f"    {status.message or 'Instalando'}: {status.progress}%")
        sleep(0.5)
    if status.state == "ready":
        console.step("Navegador instalado. Peça ao agente para abrir uma página.")
        return 0
    console.failed("Não foi possível instalar o navegador.", status.error)
    return 1
