"""Run a shell command whose whole process tree dies with it on timeout."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

MAX_OUTPUT_CHARS = 12_000

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    error: str | None = None


class _WindowsJob:
    """A Job Object with KILL_ON_JOB_CLOSE: closing it kills every process inside."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._kernel32 = kernel32
        self.handle = kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("CreateJobObjectW failed")
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(self.handle)
            self.handle = None
            raise OSError("SetInformationJobObject failed")

    def assign(self, pid: int) -> bool:
        process = self._kernel32.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not process:
            return False
        try:
            return bool(self._kernel32.AssignProcessToJobObject(self.handle, process))
        finally:
            self._kernel32.CloseHandle(process)

    def terminate(self) -> None:
        if self.handle:
            self._kernel32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


def _descendants_from(
    rows: Iterable[tuple[int, int, int]],
    root_pid: int,
    root_created: int,
    exclude: frozenset[int] = frozenset(),
) -> list[int]:
    """The PIDs provably descended from ``root_pid``. Pure: makes no system calls.

    ``rows`` are ``(pid, parent_pid, created_100ns)``. Windows keeps a dead process's parent
    PID in the process table and recycles PIDs, so a row whose parent PID now names a newer
    process is not a child of that process. A child counts only when it was created no
    earlier than its parent and no earlier than the root. A rejected node is not descended
    through. ``exclude`` (Orin itself and its ancestors) is never returned.
    """
    by_parent: dict[int, list[tuple[int, int]]] = {}
    for pid, parent_pid, created in rows:
        by_parent.setdefault(parent_pid, []).append((pid, created))
    found: list[int] = []
    seen = {root_pid}
    frontier: list[tuple[int, int]] = [(root_pid, root_created)]
    while frontier:
        parent, parent_created = frontier.pop()
        for child, created in by_parent.get(parent, []):
            if child in seen:
                continue
            seen.add(child)
            if child in exclude or created < parent_created or created < root_created:
                continue
            found.append(child)
            frontier.append((child, created))
    return found


def _windows_kernel32():  # noqa: ANN202 - ctypes handle, imported lazily
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def _windows_snapshot() -> list[tuple[int, int]]:
    """``(pid, parent_pid)`` for every process in the table, from one Toolhelp snapshot."""
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return []
    rows: list[tuple[int, int]] = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return rows


def _windows_creation_time(kernel32, handle: int) -> int | None:  # noqa: ANN001 - ctypes kernel32
    import ctypes
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    created, exited, kernel, user = FileTime(), FileTime(), FileTime(), FileTime()
    if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                    ctypes.byref(kernel), ctypes.byref(user)):
        return None
    return (int(created.high) << 32) | int(created.low)


def _orin_ancestors(parents: Mapping[int, int], start: int) -> frozenset[int]:
    chain: set[int] = set()
    current = start
    for _ in range(256):
        parent = parents.get(current)
        if parent is None or parent in chain or parent == current:
            break
        chain.add(parent)
        current = parent
    return frozenset(chain)


def _windows_descendants(process: subprocess.Popen[bytes]) -> dict[int, int]:
    """Live descendants of ``process`` as ``{pid: open process handle}``. Terminates nothing.

    Only processes that pass ``_descendants_from`` are returned. The handles stay open, so
    termination goes through the same object that was validated: a PID cannot be recycled
    under an open handle. The caller owns the handles and must close them.
    """
    kernel32 = _windows_kernel32()
    root_pid = process.pid
    root_handle = getattr(process, "_handle", None)  # Popen keeps it open, so its PID cannot be recycled
    opened_root = False
    if not isinstance(root_handle, int) or not root_handle:
        root_handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, root_pid)
        opened_root = bool(root_handle)
    if not root_handle:
        return {}
    try:
        root_created = _windows_creation_time(kernel32, root_handle)
    finally:
        if opened_root:
            kernel32.CloseHandle(root_handle)
    if root_created is None:
        return {}

    rows = _windows_snapshot()
    parents = dict(rows)
    protected = frozenset({os.getpid()}) | _orin_ancestors(parents, os.getpid())
    children: dict[int, list[int]] = {}
    for pid, parent_pid in rows:
        children.setdefault(parent_pid, []).append(pid)
    # Candidates are everything reachable by parent PID; timing is decided after opening them.
    candidates: list[int] = []
    seen = {root_pid}
    frontier = [root_pid]
    while frontier:
        for child in children.get(frontier.pop(), []):
            if child not in seen:
                seen.add(child)
                candidates.append(child)
                frontier.append(child)

    opened: dict[int, int] = {}
    timed: list[tuple[int, int, int]] = []
    for pid in candidates:
        if pid in protected:
            continue
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
        if not handle:
            continue
        created = _windows_creation_time(kernel32, handle)
        if created is None:
            kernel32.CloseHandle(handle)
            continue
        opened[pid] = handle
        timed.append((pid, parents[pid], created))
    keep = _descendants_from(timed, root_pid, root_created, exclude=protected)
    kept = {pid: opened.pop(pid) for pid in keep}
    for handle in opened.values():
        kernel32.CloseHandle(handle)
    return kept


def _terminate_handles(handles: Mapping[int, int]) -> None:
    kernel32 = _windows_kernel32()
    for handle in handles.values():
        kernel32.TerminateProcess(handle, 1)


def _close_handles(handles: Mapping[int, int]) -> None:
    kernel32 = _windows_kernel32()
    for handle in handles.values():
        kernel32.CloseHandle(handle)


def _decode(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]


def _kill_tree(process: subprocess.Popen[bytes], job: _WindowsJob | None, descendants: Mapping[int, int]) -> None:
    if os.name == "nt":
        # Validated descendants first (through the handles that were checked), then the Job, then the root.
        try:
            _terminate_handles(descendants)
        except (OSError, ValueError):
            pass
        finally:
            try:
                _close_handles(descendants)
            except (OSError, ValueError):
                pass
        if job is not None:
            try:
                job.terminate()
            except OSError:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def run_shell_command(
    command: str,
    *,
    cwd: str | None,
    env: Mapping[str, str],
    stdin_text: str,
    timeout_seconds: float,
) -> ProcessResult:
    options: dict[str, object] = {
        "shell": True,
        "cwd": cwd,
        "env": {str(key): str(value) for key, value in env.items()},
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        options["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **options)  # noqa: S602 - hooks are user-authored shell commands
    except (OSError, ValueError) as error:
        return ProcessResult(None, "", "", False, f"{type(error).__name__}: {error}")
    job: _WindowsJob | None = None
    if os.name == "nt":
        try:
            job = _WindowsJob()
            job.assign(process.pid)
        except OSError:
            job = None
    try:
        try:
            stdout, stderr = process.communicate(input=stdin_text.encode("utf-8"), timeout=max(0.1, float(timeout_seconds)))
        except subprocess.TimeoutExpired:
            descendants: dict[int, int] = {}
            if os.name == "nt":
                try:
                    descendants = _windows_descendants(process)
                except (OSError, ValueError):
                    descendants = {}
            _kill_tree(process, job, descendants)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                stdout, stderr = b"", b""
            return ProcessResult(None, _decode(stdout), _decode(stderr), True)
        return ProcessResult(process.returncode, _decode(stdout), _decode(stderr), False)
    finally:
        if job is not None:
            job.close()
