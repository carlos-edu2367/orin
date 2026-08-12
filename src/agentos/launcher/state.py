"""Single-instance detection and the record of what is running.

A pid file on its own is not evidence. Pids are recycled, a crashed launcher
leaves its file behind, and an unrelated process can inherit the number. So the
launcher holds an exclusive OS lock on a file for its entire lifetime: the kernel
releases it when the process dies, however it dies, which makes "is Orin running"
a question with a truthful answer rather than a guess.

The JSON state file beside the lock is only a description — port, url, version —
and is trusted only while the lock is actually held.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from agentos.installation import OrinPaths


@dataclass(frozen=True, slots=True)
class InstanceState:
    pid: int
    port: int
    url: str
    version: str
    profile: str
    started_at: str

    @classmethod
    def create(cls, *, port: int, url: str, version: str, profile: str) -> "InstanceState":
        return cls(os.getpid(), port, url, version, profile, datetime.now(UTC).isoformat())

    @classmethod
    def parse(cls, raw: object) -> "InstanceState | None":
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                pid=int(raw["pid"]),
                port=int(raw["port"]),
                url=str(raw["url"]),
                version=str(raw.get("version", "")),
                profile=str(raw.get("profile", "")),
                started_at=str(raw.get("started_at", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


class InstanceLock:
    """An exclusive, process-lifetime lock on one Orin installation."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._handle: IO[str] | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        """Take the lock. ``False`` means another launcher already holds it."""
        if self._handle is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._path, "a+", encoding="utf-8")  # noqa: SIM115 - held for the process lifetime
        try:
            _lock_exclusive(handle)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        except OSError:
            pass
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _unlock(handle)
        except OSError:
            pass
        finally:
            handle.close()

    def __enter__(self) -> "InstanceLock":
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def _lock_exclusive(handle: IO[str]) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: IO[str]) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_state(paths: OrinPaths, state: InstanceState) -> None:
    paths.run.mkdir(parents=True, exist_ok=True)
    temporary = paths.instance_state.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    temporary.replace(paths.instance_state)


def read_state(paths: OrinPaths) -> InstanceState | None:
    try:
        return InstanceState.parse(json.loads(paths.instance_state.read_text(encoding="utf-8")))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def clear_state(paths: OrinPaths) -> None:
    for path in (paths.instance_state, paths.stop_request):
        try:
            path.unlink()
        except OSError:
            pass


def lock_is_free(paths: OrinPaths) -> bool:
    """Probe whether any launcher currently holds this installation.

    The probe takes and immediately drops the lock, so it answers the question
    without leaving anything behind.
    """
    probe = InstanceLock(paths.instance_lock)
    if not probe.acquire():
        return False
    probe.release()
    return True


def running_instance(paths: OrinPaths) -> InstanceState | None:
    """The instance currently holding this installation, if any.

    Returns ``None`` when the lock is free — a leftover state file from a crashed
    launcher is not an instance, and is cleaned up here rather than believed.
    """
    if lock_is_free(paths):
        clear_state(paths)
        return None
    state = read_state(paths)
    if state is None:
        # Locked but undescribed: another launcher is mid-startup. It owns the
        # installation, and the caller will fail to take the lock either way.
        return None
    if not process_is_alive(state.pid):
        # Windows can hold a dying process's file lock for a moment after it is
        # gone. A recorded pid that no longer exists is not a running instance.
        clear_state(paths)
        return None
    return state


def process_is_alive(pid: int) -> bool:
    """Best-effort liveness for a pid we recorded ourselves."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            # WAIT_TIMEOUT (258) means the process is still running.
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 258
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def request_stop(paths: OrinPaths) -> None:
    """Ask a running supervisor to shut down.

    A file rather than a signal: on Windows there is no portable way to deliver
    an interrupt to an unrelated console process, and a request the supervisor
    polls for behaves identically on every platform.
    """
    paths.run.mkdir(parents=True, exist_ok=True)
    paths.stop_request.write_text(f"{os.getpid()} {datetime.now(UTC).isoformat()}\n", encoding="utf-8")


def stop_requested(paths: OrinPaths) -> bool:
    return paths.stop_request.exists()


def clear_stop_request(paths: OrinPaths) -> None:
    try:
        paths.stop_request.unlink()
    except OSError:
        pass


def kill_process_tree(pid: int) -> bool:
    """Last-resort termination of a recorded launcher that ignored the request."""
    if not process_is_alive(pid):
        return True
    if os.name == "nt":
        from subprocess import DEVNULL, run

        result = run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=DEVNULL, stderr=DEVNULL, check=False)  # noqa: S603, S607
        return result.returncode == 0
    import signal

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return False
    return True


def current_executable_hint() -> str:
    """How this launcher was invoked. Recorded for diagnostics only."""
    return sys.executable


__all__ = [
    "InstanceLock",
    "InstanceState",
    "clear_state",
    "clear_stop_request",
    "kill_process_tree",
    "lock_is_free",
    "process_is_alive",
    "read_state",
    "request_stop",
    "running_instance",
    "stop_requested",
    "write_state",
]
