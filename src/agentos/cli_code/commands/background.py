"""Background runs (``orin code -p ... --bg``), ``logs``, ``stop``, ``rm``, ``attach`` and ``purge``.

``--bg`` starts the same command again, detached, without ``--bg``; its output goes to a log file and a small record
(``<ORIN_DATA_DIR>/cli-background/<id>.json``: pid, folder, command) lets the other subcommands find it. ``attach``
follows the log until the run ends (a background run has no stdin: it was started with ``-p``). ``purge`` removes the
data ``orin code`` keeps (session index, transcripts, host requests, background runs) after ``--yes``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from ..args import CliUsageError
from ..errors import CliRuntimeError


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def background_root() -> Path:
    from agentos.installation import orin_paths

    return orin_paths().data / "cli-background"


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000


def _alive(pid: int) -> bool:
    from agentos.launcher.state import process_is_alive

    return process_is_alive(pid)


def process_created(pid: int) -> int | str | None:
    """When the process with this pid started, read the same way for a record and for a later check (W6-R3).

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
    try:
        result = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False, timeout=10)  # noqa: S603, S607
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _same_process(record: dict[str, Any]) -> bool:
    """True only when the record's pid is alive and its creation time is the one recorded at start.

    A record without a creation time (written before this check existed), or one whose time cannot be read, is
    treated as a different process: it is reported as finished and nothing is killed for it (W6-R3).
    """
    pid = int(record.get("pid") or 0)
    recorded = record.get("created")
    if pid <= 0 or recorded is None or recorded == "" or not _alive(pid):
        return False
    current = process_created(pid)
    return current is not None and current == recorded


def _record(run_id: str) -> dict[str, Any]:
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise CliRuntimeError(f"no background run {run_id!r}")
    try:
        data = json.loads((background_root() / f"{run_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CliRuntimeError(f"no background run {run_id!r}") from error
    return data


def background_runs() -> list[dict[str, Any]]:
    runs = []
    root = background_root()
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data["state"] = "running" if _same_process(data) else "finished"
        runs.append(data)
    return runs


def start_background(argv: list[str], *, cwd: Path, stdout: TextIO) -> int:
    """Start ``orin code <argv without --bg>`` detached; print its id."""
    run_id = f"bg_{uuid.uuid4().hex[:12]}"
    root = background_root()
    root.mkdir(parents=True, exist_ok=True)
    log = root / f"{run_id}.log"
    args = [item for item in argv if item != "--bg"]
    # A known session id lets `stop` cancel the run's turn in Orin, not only the process that follows it.
    session_id = next((item.split("=", 1)[1] for item in args if item.startswith("--session-id=")), None)
    if session_id is None and "--session-id" in args and args.index("--session-id") + 1 < len(args):
        session_id = args[args.index("--session-id") + 1]
    if session_id is None and not any(item in args for item in ("-c", "--continue", "-r", "--resume")) and not any(
        item.startswith("--resume=") for item in args
    ):
        session_id = str(uuid.uuid4())
        args = [*args, f"--session-id={session_id}"]
    executable = Path(sys.argv[0]) if Path(sys.argv[0]).stem.lower() == "orin-code" else None
    command = [str(executable)] + args if executable is not None and executable.is_file() else [sys.executable, "-m", "agentos.cli_code.main", *args]
    flags: dict[str, Any] = {}
    if os.name == "nt":
        flags["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        flags["start_new_session"] = True
    with open(log, "wb") as handle:
        process = subprocess.Popen(command, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, close_fds=True, **flags)
    record = {"id": run_id, "pid": process.pid, "created": process_created(process.pid), "cwd": str(cwd), "args": args,
              "log": str(log), "session_id": session_id, "started_at": datetime.now(UTC).isoformat()}
    (root / f"{run_id}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    stdout.write(f"{run_id}\n")
    return 0


def _stop(record: dict[str, Any]) -> bool:
    """Cancel the run's turn and stop its process tree, but only when the record still names that process (W6-R3)."""
    if not _same_process(record):
        return False
    _cancel_turn(record)
    return _kill_verified_windows(record) if os.name == "nt" else _kill_verified_posix(record)


def _kill_verified_windows(record: dict[str, Any]) -> bool:
    """Terminate the run and its descendants through handles opened and validated first; never by bare pid."""
    from types import SimpleNamespace

    from agentos.hooks.processes import _close_handles, _windows_creation_time, _windows_descendants, _windows_kernel32

    kernel32 = _windows_kernel32()
    pid = int(record["pid"])
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
    if not handle:
        return False
    descendants: dict[int, int] = {}
    try:
        if _windows_creation_time(kernel32, handle) != record.get("created"):
            return False  # the pid now belongs to another process
        descendants = _windows_descendants(SimpleNamespace(pid=pid, _handle=handle))
        for child in descendants.values():
            kernel32.TerminateProcess(child, 1)
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        _close_handles(descendants)
        kernel32.CloseHandle(handle)


def _kill_verified_posix(record: dict[str, Any]) -> bool:
    """SIGKILL the run's process group (it starts its own session); called only after the check."""
    import signal

    pid = int(record["pid"])
    try:
        if os.getpgid(pid) != pid:
            return False
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        return False
    return True


def _cancel_turn(record: dict[str, Any]) -> None:
    """Cancel the run's turn in Orin (best effort: the process is stopped either way)."""
    from agentos.installation import orin_paths

    from ..runtime import connect_local
    from ..sessions import SessionIndex

    session = SessionIndex(orin_paths().data).find(str(record.get("session_id") or ""))
    if session is None:
        return
    try:
        client = connect_local(orin_paths(), start=False)
    except CliRuntimeError:
        return
    try:
        client.post(f"/v1/conversations/{session.conversation_id}/cancel", {})
    except CliRuntimeError:
        pass
    finally:
        client.close()


def run(name: str, argv: list[str], *, cwd: Path, stdout: TextIO, stderr: TextIO) -> int:
    parser = _Parser(prog=f"orin code {name}", allow_abbrev=False)
    if name == "purge":
        parser.add_argument("--yes", action="store_true")
        options = parser.parse_args(argv)
        if not options.yes:
            raise CliUsageError("purge removes orin code's sessions, transcripts and background runs; confirm with --yes")
        from agentos.installation import orin_paths

        data = orin_paths().data
        for run_record in background_runs():
            _stop(run_record)
        for target in ("cli-transcripts", "cli-host-requests", "cli-background"):
            shutil.rmtree(data / target, ignore_errors=True)
        try:
            (data / "cli-sessions.json").unlink()
        except FileNotFoundError:
            pass
        stdout.write("orin code's local data was removed (the conversations stay in Orin).\n")
        return 0
    parser.add_argument("id")
    if name == "logs":
        parser.add_argument("-f", "--follow", action="store_true")
    options = parser.parse_args(argv)
    record = _record(options.id)
    log = Path(record["log"])
    if name == "stop":
        stdout.write(f"{options.id}: {'stopped' if _stop(record) else 'had already finished'}\n")
        return 0
    if name == "rm":
        _stop(record)
        for path in (log, background_root() / f"{options.id}.json"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        stdout.write(f"{options.id}: removed\n")
        return 0
    follow = name == "attach" or getattr(options, "follow", False)
    position = 0
    while True:
        try:
            with open(log, "rb") as handle:
                handle.seek(position)
                chunk = handle.read()
        except OSError:
            chunk = b""
        if chunk:
            position += len(chunk)
            stdout.write(chunk.decode("utf-8", errors="replace"))
            stdout.flush()
        if not follow or not _same_process(record):
            if follow and chunk:
                continue
            return 0
        time.sleep(0.5)


__all__ = ["background_runs", "process_created", "run", "start_background"]
