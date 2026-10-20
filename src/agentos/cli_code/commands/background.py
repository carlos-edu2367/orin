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


def _alive(pid: int) -> bool:
    from agentos.launcher.state import process_is_alive

    return process_is_alive(pid)


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
        data["state"] = "running" if _alive(int(data.get("pid") or 0)) else "finished"
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
    record = {"id": run_id, "pid": process.pid, "cwd": str(cwd), "args": args, "log": str(log),
              "session_id": session_id, "started_at": datetime.now(UTC).isoformat()}
    (root / f"{run_id}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    stdout.write(f"{run_id}\n")
    return 0


def _stop(record: dict[str, Any]) -> bool:
    pid = int(record.get("pid") or 0)
    if not _alive(pid):
        return False
    _cancel_turn(record)
    from agentos.launcher.state import kill_process_tree

    kill_process_tree(pid)
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
        if not follow or not _alive(int(record.get("pid") or 0)):
            if follow and chunk:
                continue
            return 0
        time.sleep(0.5)


__all__ = ["background_runs", "run", "start_background"]
