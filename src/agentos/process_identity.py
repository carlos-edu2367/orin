"""Which process a stored pid names, and stopping it only when it is still that process (W6-R3, W7).

A pid alone is not an identity: the OS reuses it. Records that outlive a process (``orin code --bg`` runs, background
shells) keep the creation time read here, and anything that stops one checks it again first.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000


def process_created(pid: int) -> int | str | None:
    """When the process with this pid started, read the same way for a record and for a later check.

    Windows: the creation time from ``GetProcessTimes``; POSIX: field 22 of ``/proc/<pid>/stat``, or where there is
    no ``/proc``, ``ps -o lstart=``. None when it cannot be read.
    """
    if pid <= 0:
        return None
    if os.name == "nt":
        from agentos.hooks.processes import _windows_creation_time, _windows_kernel32

        kernel32 = _windows_kernel32()
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            return _windows_creation_time(kernel32, handle)
        finally:
            kernel32.CloseHandle(handle)
    stat = Path(f"/proc/{pid}/stat")
    if stat.is_file():
        try:
            fields = stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            return int(fields[19])  # starttime: field 22 counting pid and comm
        except (OSError, IndexError, ValueError):
            return None
    if Path("/proc").is_dir():
        return None  # a system with /proc and no entry for this pid: the process does not exist
    try:
        result = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False, timeout=10)  # noqa: S603, S607
    except Exception:  # noqa: BLE001 - an identity that cannot be read is None (nothing is ever stopped for it)
        return None
    value = result.stdout.strip()
    return value or None


def same_process(pid: int, created: object) -> bool:
    """True only when ``pid`` is alive and started at ``created``. A missing or unreadable time is never a match."""
    from agentos.launcher.state import process_is_alive

    if pid <= 0 or created is None or created == "" or not process_is_alive(pid):
        return False
    current = process_created(pid)
    return current is not None and current == created


def kill_verified(pid: int, created: object, *, group_only: bool = False) -> bool:
    """Stop the process (and its tree) only if it is still the one that started at ``created``.

    Windows: through a handle opened and validated first, with its validated descendants; never by bare pid.
    POSIX: its process group when it leads one (``group_only`` requires that), else the process itself.
    """
    if not same_process(pid, created):
        return False
    if os.name == "nt":
        return _kill_windows(pid, created)
    import signal

    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
            return True
        if group_only:
            return False
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return False
    return True


def _kill_windows(pid: int, created: object) -> bool:
    from types import SimpleNamespace

    from agentos.hooks.processes import _close_handles, _windows_creation_time, _windows_descendants, _windows_kernel32

    kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
    if not handle:
        return False
    descendants: dict[int, int] = {}
    try:
        if _windows_creation_time(kernel32, handle) != created:
            return False  # the pid now belongs to another process
        descendants = _windows_descendants(SimpleNamespace(pid=pid, _handle=handle))
        for child in descendants.values():
            kernel32.TerminateProcess(child, 1)
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        _close_handles(descendants)
        kernel32.CloseHandle(handle)


__all__ = ["kill_verified", "process_created", "same_process"]
