"""A rewind and the turns of one workspace folder never overlap (W4-R11).

Two layers. Inside one process, a count of running turns and a rewind flag per folder, under a Condition. Across
processes (the desktop app runs the API and the chat worker separately), marker files in the run directory: a turn
leaves ``<hash>.turn.<pid>.<uuid>`` and a rewind leaves ``<hash>.rewind``, where ``<hash>`` names the folder.

A turn writes its marker first and then checks for a live rewind marker; a rewind writes its marker first and then
checks for live turn markers. Whichever writes second sees the other, so a turn and a rewind cannot both proceed. A
marker whose pid is not running is stale: it is ignored and removed when seen.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agentos.installation.paths import orin_paths

_CONDITION = threading.Condition()
_TURNS: dict[str, int] = {}
_REWINDING: set[str] = set()
_POLL_SECONDS = 0.2
# A rewind marker that is still empty (its owner has not written its pid yet) is trusted this long, then it is stale.
_UNWRITTEN_SECONDS = 5.0


class WorkspaceBusy(RuntimeError):
    """A turn of this folder is running, in this process or another, or a rewind holds it."""


@dataclass(frozen=True, slots=True)
class TurnToken:
    """What ``enter_turn`` returns: the folder key, and this turn's marker file, which ``exit_turn`` removes."""

    key: str
    marker: Path


def folder_key(root: Path | str) -> str:
    return os.path.normcase(str(Path(root).resolve()))


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _lock_dir() -> Path:
    folder = orin_paths().run / "workspace-locks"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _pid_alive(pid: int) -> bool:
    """Whether ``pid`` is running. Our own pid counts as running. Windows asks the kernel, never ``os.kill``."""
    if pid == os.getpid():
        return True
    if pid <= 0:
        return False
    if os.name == "nt":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: the process exists
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass  # already gone, or left for the next check to find stale


def _rewind_holder_alive(path: Path) -> bool:
    """Whether the process that wrote this rewind marker still runs. An empty marker is trusted until it is old."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    except OSError:
        return True  # cannot read it: fail closed
    if text.isdigit():
        return _pid_alive(int(text))
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age < _UNWRITTEN_SECONDS


def _rewind_marker_live(folder: Path, digest: str) -> bool:
    rewind = folder / f"{digest}.rewind"
    if not rewind.exists():
        return False
    if _rewind_holder_alive(rewind):
        return True
    _remove(rewind)  # stale: its process is gone
    return False


def _live_turn_marker(folder: Path, digest: str) -> bool:
    """Whether a turn of this folder runs in any process. Turn markers of dead processes are removed."""
    live = False
    for path in folder.glob(f"{digest}.turn.*"):
        parts = path.name.split(".")  # <hash>.turn.<pid>.<uuid>
        try:
            pid = int(parts[2]) if len(parts) == 4 and parts[1] == "turn" else None
        except ValueError:
            pid = None
        if pid is None:
            continue
        if _pid_alive(pid):
            live = True
        else:
            _remove(path)
    return live


def _write_turn_marker(folder: Path, digest: str) -> Path:
    path = folder / f"{digest}.turn.{os.getpid()}.{uuid4().hex}"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    return path


def _claim_rewind(folder: Path, digest: str, key: str) -> Path:
    rewind = folder / f"{digest}.rewind"
    for _attempt in range(2):
        try:
            fd = os.open(rewind, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _rewind_holder_alive(rewind):
                raise WorkspaceBusy(key) from None
            _remove(rewind)  # stale: retry once
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        if _live_turn_marker(folder, digest):
            _remove(rewind)
            raise WorkspaceBusy(key)
        return rewind
    raise WorkspaceBusy(key)


def enter_turn(root: Path | str) -> TurnToken:
    """Count a running turn of ``root`` in this process and in every other one. Waits while a rewind holds the folder."""
    key = folder_key(root)
    folder = _lock_dir()
    digest = _digest(key)
    while True:
        with _CONDITION:
            while key in _REWINDING:
                _CONDITION.wait(_POLL_SECONDS)
        marker = _write_turn_marker(folder, digest)
        if not _rewind_marker_live(folder, digest):
            with _CONDITION:
                _TURNS[key] = _TURNS.get(key, 0) + 1
            return TurnToken(key, marker)
        _remove(marker)
        time.sleep(_POLL_SECONDS)


def exit_turn(token: TurnToken) -> None:
    _remove(token.marker)
    with _CONDITION:
        count = _TURNS.get(token.key, 0) - 1
        if count > 0:
            _TURNS[token.key] = count
        else:
            _TURNS.pop(token.key, None)
        _CONDITION.notify_all()


@contextmanager
def exclusive_folder(root: Path | str) -> Iterator[None]:
    """Hold ``root`` for one rewind. Raises WorkspaceBusy when a turn of it runs, here or in another process, or when
    another rewind holds it."""
    key = folder_key(root)
    with _CONDITION:
        if key in _REWINDING or _TURNS.get(key, 0) > 0:
            raise WorkspaceBusy(key)
        _REWINDING.add(key)
    marker: Path | None = None
    try:
        folder = _lock_dir()
        marker = _claim_rewind(folder, _digest(key), key)
        yield
    finally:
        if marker is not None:
            _remove(marker)
        with _CONDITION:
            _REWINDING.discard(key)
            _CONDITION.notify_all()


__all__ = ["TurnToken", "WorkspaceBusy", "enter_turn", "exclusive_folder", "exit_turn", "folder_key"]
