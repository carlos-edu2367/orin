"""The browser engine is an optional download, not part of the package.

Chromium is by far the largest piece of an Orin release, and most people never
ask the agent to drive a browser. It is therefore fetched on demand, into the
per-user cache (``PLAYWRIGHT_BROWSERS_PATH``), by the same Playwright driver the
Python package already ships. This module answers "is it there?", installs it,
and reports progress; it never imports Playwright's browser API.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Lock, Thread
from typing import Callable, Sequence

INSTALL_COMMAND = "orin browser install"
SETTINGS_HINT = "Configurações > Navegador > Instalar"

# Playwright writes this marker only once a browser finished downloading, so a
# half-finished or interrupted install is never mistaken for a usable one.
_MARKER = "INSTALLATION_COMPLETE"
_BROWSER_DIRS = ("chromium-*", "chromium_headless_shell-*")
_PERCENT = re.compile(r"(\d{1,3})%")
_LOG_LINES = 12

CommandFactory = Callable[[], Sequence[str]]


_Path = type(Path())


def default_browsers_path() -> Path:
    """Where Playwright itself puts browsers when ``PLAYWRIGHT_BROWSERS_PATH`` is unset."""
    if os.name == "nt":
        return _Path(os.environ.get("LOCALAPPDATA") or _Path.home() / "AppData" / "Local") / "ms-playwright"
    if sys.platform == "darwin":
        return _Path.home() / "Library" / "Caches" / "ms-playwright"
    return _Path(os.environ.get("XDG_CACHE_HOME") or _Path.home() / ".cache") / "ms-playwright"


def browsers_path() -> Path:
    # An installed Orin always exports the variable (pointing at its own cache);
    # a development checkout and CI usually do not, and then Playwright's own
    # default is where the browser lives.
    value = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    return _Path(value) if value and value != "0" else default_browsers_path()


def playwright_package_present() -> bool:
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


_HEADLESS_BINARIES = (
    "chrome-headless-shell.exe",
    "headless_shell.exe",
    "chrome-headless-shell",
    "headless_shell",
)
_FULL_BINARIES = (
    "chrome.exe",
    "chrome",
    "Chromium",
)


def _revision_key(directory: Path) -> int:
    _, _, suffix = directory.name.rpartition("-")
    return int(suffix) if suffix.isdigit() else -1


def _find_binary_in_dir(directory: Path, binary_names: tuple[str, ...]) -> Path | None:
    for name in binary_names:
        direct = directory / name
        if direct.is_file():
            return direct
        for candidate in sorted(directory.glob(f"*/{name}")):
            if candidate.is_file():
                return candidate
        for candidate in sorted(directory.glob(f"*/*.app/Contents/MacOS/{name}")):
            if candidate.is_file():
                return candidate
    return None


def resolve_chromium_executable(headless: bool = True, root: Path | None = None) -> Path | None:
    """Locate an installed Chromium or headless-shell binary even when its revision differs from Playwright's default."""
    target = root if root is not None else browsers_path()
    if not target.is_dir():
        return None
    search_plan = (
        (("chromium_headless_shell-*", _HEADLESS_BINARIES), ("chromium-*", _FULL_BINARIES))
        if headless
        else (("chromium-*", _FULL_BINARIES), ("chromium_headless_shell-*", _HEADLESS_BINARIES))
    )
    for require_marker in (True, False):
        for pattern, binaries in search_plan:
            directories = sorted(
                (d for d in target.glob(pattern) if d.is_dir()),
                key=_revision_key,
                reverse=True,
            )
            for directory in directories:
                if require_marker and not (directory / _MARKER).is_file():
                    continue
                found = _find_binary_in_dir(directory, binaries)
                if found is not None:
                    return found
    return None


def chromium_installed(root: Path | None = None) -> bool:
    target = root if root is not None else browsers_path()
    if not target.is_dir():
        return False
    if any((directory / _MARKER).is_file() for pattern in _BROWSER_DIRS for directory in target.glob(pattern)):
        return True
    return resolve_chromium_executable(headless=True, root=target) is not None


def _driver_command() -> list[str]:
    """The bundled Playwright driver's ``install chromium`` command.

    Used instead of ``python -m playwright`` because a frozen Orin has no
    ``python`` to run, only the driver the package carries.
    """
    from playwright._impl._driver import compute_driver_executable

    return [*compute_driver_executable(), "install", "chromium"]


def _driver_environment() -> dict[str, str]:
    from playwright._impl._driver import get_driver_env

    return {**get_driver_env(), **{key: value for key, value in os.environ.items() if key == "PLAYWRIGHT_BROWSERS_PATH"}}


@dataclass(frozen=True, slots=True)
class BrowserEngineStatus:
    state: str  # "ready" | "missing" | "installing" | "failed" | "unsupported"
    progress: int | None = None
    message: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "installed": self.state == "ready",
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "install_command": INSTALL_COMMAND,
        }


class BrowserEngineInstaller:
    """One background install at a time, observable by polling ``status()``."""

    def __init__(self, *, command: CommandFactory = _driver_command, environment: Callable[[], dict[str, str]] = _driver_environment) -> None:
        self._command = command
        self._environment = environment
        self._lock = Lock()
        self._thread: Thread | None = None
        self._progress: int | None = None
        self._message: str | None = None
        self._error: str | None = None
        self._log: deque[str] = deque(maxlen=_LOG_LINES)

    def status(self) -> BrowserEngineStatus:
        with self._lock:
            installing = self._thread is not None and self._thread.is_alive()
            if installing:
                return BrowserEngineStatus("installing", self._progress, self._message)
            error = self._error
        if chromium_installed():
            return BrowserEngineStatus("ready")
        if not playwright_package_present():
            return BrowserEngineStatus("unsupported", error="O componente de navegador não faz parte desta instalação.")
        if error:
            return BrowserEngineStatus("failed", error=error)
        return BrowserEngineStatus("missing")

    def start(self) -> BrowserEngineStatus:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return BrowserEngineStatus("installing", self._progress, self._message)
            if not chromium_installed() and playwright_package_present():
                self._progress, self._message, self._error = 0, "Preparando o download", None
                self._log.clear()
                self._thread = Thread(target=self._run, name="orin-browser-install", daemon=True)
                self._thread.start()
        return self.status()

    def _run(self) -> None:
        env = {
            **{
                k: v
                for k, v in os.environ.items()
                if k.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "HOME"}
            },
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            **self._environment(),
        }
        try:
            process = subprocess.Popen(
                list(self._command()), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            self._finish(f"Não foi possível iniciar o instalador do navegador ({type(error).__name__}).")
            return
        assert process.stdout is not None
        buffer = b""
        while chunk := process.stdout.read1(4096):  # type: ignore[attr-defined]
            buffer += chunk
            *lines, buffer = re.split(rb"[\r\n]+", buffer)
            for line in lines:
                self._observe(line.decode("utf-8", "replace").strip())
        if buffer:
            self._observe(buffer.decode("utf-8", "replace").strip())
        code = process.wait()
        if code == 0 and chromium_installed():
            self._finish(None)
            return
        detail = " ".join(self._log)[-300:]
        self._finish(f"A instalação do navegador terminou com erro (código {code}). {detail}".strip())

    def _observe(self, line: str) -> None:
        if not line:
            return
        with self._lock:
            self._log.append(line)
            match = _PERCENT.search(line)
            if match:
                self._progress = min(100, int(match.group(1)))
                self._message = "Baixando o navegador"
            elif line.lower().startswith("downloading"):
                self._message = "Baixando o navegador"
            elif "extract" in line.lower() or "installing" in line.lower():
                self._message = "Instalando"

    def _finish(self, error: str | None) -> None:
        with self._lock:
            self._error = error
            self._progress = 100 if error is None else self._progress
            self._message = None


_installer = BrowserEngineInstaller()


def engine_installer() -> BrowserEngineInstaller:
    return _installer


def engine_missing_for_agent() -> bool:
    """True when the browser is simply not installed (as opposed to unsupported)."""
    return playwright_package_present() and not chromium_installed()


def agent_install_guidance() -> str:
    """What the agent should tell the person, in the person's terms."""
    return (
        "O navegador (Chromium) não está instalado neste computador, então não consigo abrir páginas dinâmicas. "
        f"Para instalar: abra {SETTINGS_HINT} e clique em Instalar (leva alguns minutos e cerca de 150 MB), "
        f"ou rode `{INSTALL_COMMAND}` no terminal. Depois é só pedir de novo."
    )


__all__ = [
    "BrowserEngineInstaller", "BrowserEngineStatus", "INSTALL_COMMAND", "agent_install_guidance", "browsers_path",
    "chromium_installed", "engine_installer", "engine_missing_for_agent", "playwright_package_present",
    "resolve_chromium_executable",
]
