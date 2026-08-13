"""Launching the Electron shell without moving launcher ownership into Node."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agentos.installation import OrinPaths, RuntimeProfile

from .desktop_status import DesktopStatusWriter


class DesktopUnavailable(RuntimeError):
    """Electron has not been installed or its packaged executable is missing."""


@dataclass(slots=True)
class DesktopProcess:
    process: subprocess.Popen[bytes]
    log_path: Path


def launch_desktop(
    paths: OrinPaths,
    profile: RuntimeProfile,
    status: DesktopStatusWriter,
    *,
    devtools: bool = False,
    focus_only: bool = False,
) -> DesktopProcess:
    """Start Electron with a status file it can read, never through a shell."""
    command = list(_electron_command(profile))
    command.extend(["--status-file", str(status.path)])
    if devtools:
        command.append("--devtools")
    if focus_only:
        command.append("--focus-only")

    log_path = paths.logs / "desktop.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab", buffering=0)  # noqa: SIM115 - inherited by the child until it exits
    try:
        process = subprocess.Popen(  # noqa: S603 - paths come from this installation, never a renderer or CLI string
            command,
            cwd=str(_desktop_root(profile)),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
    except Exception:
        log.close()
        raise
    return DesktopProcess(process, log_path)


def focus_desktop(paths: OrinPaths, profile: RuntimeProfile) -> bool:
    """Ask Electron's single-instance handler to restore its existing window."""
    status_path = paths.run / DesktopStatusWriter.filename
    if not status_path.is_file():
        return False
    try:
        launch_desktop(paths, profile, _StatusPath(status_path), focus_only=True)
    except DesktopUnavailable:
        return False
    return True


class _StatusPath:
    """Only the path is needed when requesting focus from a second CLI call."""

    def __init__(self, path: Path) -> None:
        self.path = path


def _desktop_root(profile: RuntimeProfile) -> Path:
    return (profile.repository or profile.root) / "desktop"


def _electron_command(profile: RuntimeProfile) -> tuple[str, ...]:
    configured = os.getenv("ORIN_ELECTRON_EXECUTABLE")
    if configured:
        executable = Path(configured).expanduser()
        if executable.is_file():
            return (str(executable),)
        raise DesktopUnavailable(f"ORIN_ELECTRON_EXECUTABLE does not exist: {executable}")

    root = _desktop_root(profile)
    if profile.repository is not None:
        executable = root / "node_modules" / ".bin" / ("electron.cmd" if os.name == "nt" else "electron")
        if executable.is_file():
            return (str(executable), str(root))
        raise DesktopUnavailable(
            "Electron is not installed. Run 'npm --prefix desktop install' once, then run 'orin --desktop' again."
        )

    # In the packaged Electron layout, ``orin.exe`` lives in
    # ``resources/runtime`` while the host executable is one level above in
    # ``resources``. Electron Builder keeps ``Orin Desktop.exe`` at the root of
    # ``win-unpacked``, two levels above the frozen runtime. Development keeps
    # using the local Electron binary above.
    host_root = profile.root.parent.parent if getattr(__import__("sys"), "frozen", False) else profile.root
    executable = host_root / "Orin Desktop.exe" if os.name == "nt" else host_root / "Orin Desktop"
    if executable.is_file():
        return (str(executable),)
    raise DesktopUnavailable(
        f"The Orin Desktop shell is missing ({executable}). Reinstall Orin Desktop or set ORIN_ELECTRON_EXECUTABLE."
    )


__all__ = ["DesktopProcess", "DesktopUnavailable", "focus_desktop", "launch_desktop"]
