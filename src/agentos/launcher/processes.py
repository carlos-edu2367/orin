"""Child process supervision, with no orphans as a hard requirement.

Two independent mechanisms, because on Windows one is not enough:

* **Graceful** — each child is created in its own process group, so it can be
  sent a console break and shut itself down properly while the launcher's own
  Ctrl+C stays under the launcher's control.
* **Guaranteed** — every child is assigned to a job object created with
  ``KILL_ON_JOB_CLOSE``. The kernel closes that handle when the launcher exits,
  by any route including a hard kill, and everything inside the job dies with
  it. Grandchildren count: a process spawned by a child inherits the job, which
  is what stops an npm shim or a gateway from surviving its parent.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic, sleep
from typing import IO, Sequence

_WINDOWS = os.name == "nt"


class ProcessGroup:
    """Owns every process the launcher starts.

    On non-Windows platforms this degrades to per-process session groups, which
    is the established way to terminate a tree there.
    """

    def __init__(self) -> None:
        self._job = self._create_job() if _WINDOWS else None

    @staticmethod
    def _create_job() -> int | None:
        try:
            import ctypes
            from ctypes import wintypes

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
                )]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel32 = ctypes.windll.kernel32
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return None
            information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            information.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                job, 9, ctypes.byref(information), ctypes.sizeof(information)
            ):
                kernel32.CloseHandle(job)
                return None
            return job
        except Exception:
            # Losing the job object costs the guarantee, not the launcher. The
            # graceful path still terminates every child it knows about.
            return None

    def adopt(self, pid: int) -> bool:
        if self._job is None:
            return False
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # PROCESS_SET_QUOTA | PROCESS_TERMINATE
            handle = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)
            if not handle:
                return False
            try:
                return bool(kernel32.AssignProcessToJobObject(self._job, handle))
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False

    def terminate_all(self) -> None:
        """Kill anything still inside the job. Used only as a backstop."""
        if self._job is None:
            return
        try:
            import ctypes

            ctypes.windll.kernel32.TerminateJobObject(self._job, 1)
        except Exception:
            pass

    def close(self) -> None:
        if self._job is None:
            return
        try:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._job)
        except Exception:
            pass
        finally:
            self._job = None


@dataclass
class SupervisedProcess:
    """One child, its log file, and the last thing it said before dying."""

    name: str
    command: tuple[str, ...]
    log_path: Path
    process: subprocess.Popen[bytes] | None = None
    _log: IO[bytes] | None = field(default=None, repr=False)

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process is not None else None

    def start(self, *, environment: dict[str, str], cwd: Path, group: ProcessGroup) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "ab", buffering=0)  # noqa: SIM115 - closed in stop()
        self._log.write(f"\n--- {self.name} started ---\n".encode())
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if _WINDOWS else 0
        self.process = subprocess.Popen(  # noqa: S603 - command comes from the runtime profile, never from user input
            list(self.command),
            cwd=str(cwd),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
            start_new_session=not _WINDOWS,
            close_fds=True,
        )
        group.adopt(self.process.pid)

    def exited(self) -> int | None:
        if self.process is None:
            return None
        return self.process.poll()

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def tail(self, lines: int = 12) -> str:
        """The end of this child's log, for an error message worth reading."""
        try:
            content = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(content[-lines:])

    def stop(self, *, grace: float = 8.0) -> None:
        process = self.process
        if process is None:
            self._close_log()
            return
        if process.poll() is None:
            self._interrupt(process)
            if not self._wait(process, grace):
                process.terminate()
                if not self._wait(process, 3.0):
                    process.kill()
                    self._wait(process, 2.0)
        self._close_log()

    @staticmethod
    def _interrupt(process: subprocess.Popen[bytes]) -> None:
        try:
            if _WINDOWS:
                os.kill(process.pid, signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGTERM)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _wait(process: subprocess.Popen[bytes], timeout: float) -> bool:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            if process.poll() is not None:
                return True
            sleep(0.1)
        return process.poll() is not None

    def _close_log(self) -> None:
        log, self._log = self._log, None
        if log is not None:
            try:
                log.close()
            except OSError:
                pass


def child_environment(base: dict[str, str] | None, overrides: dict[str, str]) -> dict[str, str]:
    """Environment for a child, built from the launcher's own.

    Secrets travel here and only here — never on a command line, where any other
    user on the machine can read them out of the process table.
    """
    environment = dict(base if base is not None else os.environ)
    environment.update(overrides)
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    environment.setdefault("PYTHONUNBUFFERED", "1")
    return environment


def running_under_frozen_build() -> bool:
    return bool(getattr(sys, "frozen", False))


def format_command(command: Sequence[str]) -> str:
    return " ".join(command)


__all__ = [
    "ProcessGroup",
    "SupervisedProcess",
    "child_environment",
    "format_command",
    "running_under_frozen_build",
]
