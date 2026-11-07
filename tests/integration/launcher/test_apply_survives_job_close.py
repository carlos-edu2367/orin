"""Integration test proving how Windows Job Objects affect child processes spawned during update apply."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentos.launcher.processes import ProcessGroup
from agentos.launcher.state import process_is_alive

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object behavior only applies on Windows")

# On Windows virtualenvs, sys.executable is venvlauncher.exe, which spawns the
# real python.exe in its own job before ProcessGroup.adopt() runs. Using the base
# interpreter models a real direct child (like Orin Desktop.exe -> orin.exe).
_PYTHON = getattr(sys, "_base_executable", sys.executable)

_GRANDCHILD_CODE = """
import os, pathlib, time, sys
sentinel = pathlib.Path(sys.argv[1])
for tick in range(200):
    sentinel.write_text(f"{os.getpid()}:{tick}", encoding="utf-8")
    time.sleep(0.05)
"""

_CHILD_CODE = """
import subprocess, sys, time
sentinel = sys.argv[1]
flags = int(sys.argv[2])
grandchild_code = sys.argv[3]
sys.stdin.readline()
subprocess.Popen(
    [sys.executable, "-c", grandchild_code, sentinel],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=flags,
)
time.sleep(30)
"""


def _wait_for_sentinel(sentinel: Path, parent: subprocess.Popen[bytes], timeout: float = 5.0) -> tuple[int, int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sentinel.is_file():
            raw = sentinel.read_text(encoding="utf-8").strip()
            if ":" in raw:
                pid_str, tick_str = raw.split(":", 1)
                return int(pid_str), int(tick_str)
        if parent.poll() is not None:
            stderr = parent.stderr.read().decode("utf-8", errors="replace") if parent.stderr else ""
            raise RuntimeError(f"adopted child exited before spawning grandchild (code {parent.returncode}): {stderr}")
        time.sleep(0.02)
    raise TimeoutError("grandchild did not write sentinel in time")


def _terminate_pid(pid: int | None) -> None:
    if pid is None or not process_is_alive(pid):
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def test_child_of_adopted_process_dies_when_job_closes(tmp_path: Path) -> None:
    sentinel = tmp_path / "sentinel.txt"
    group = ProcessGroup()
    child: subprocess.Popen[bytes] | None = None
    grandchild_pid: int | None = None
    try:
        child = subprocess.Popen(
            [_PYTHON, "-c", _CHILD_CODE, str(sentinel), "0", _GRANDCHILD_CODE],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        assert group.adopt(child.pid) is True
        assert child.stdin is not None
        child.stdin.write(b"go\n")
        child.stdin.flush()

        grandchild_pid, _ = _wait_for_sentinel(sentinel, child)
        assert process_is_alive(grandchild_pid)

        group.close()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and process_is_alive(grandchild_pid):
            time.sleep(0.05)

        assert not process_is_alive(grandchild_pid)
        snapshot = sentinel.read_text(encoding="utf-8")
        time.sleep(0.25)
        assert sentinel.read_text(encoding="utf-8") == snapshot
    finally:
        group.close()
        if child is not None and child.poll() is None:
            child.kill()
        _terminate_pid(grandchild_pid)


def test_breakaway_grandchild_survives_job_close(tmp_path: Path) -> None:
    sentinel = tmp_path / "sentinel_breakaway.txt"
    group = ProcessGroup()
    child: subprocess.Popen[bytes] | None = None
    grandchild_pid: int | None = None
    flags = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    try:
        child = subprocess.Popen(
            [_PYTHON, "-c", _CHILD_CODE, str(sentinel), str(flags), _GRANDCHILD_CODE],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        assert group.adopt(child.pid) is True
        assert child.stdin is not None
        child.stdin.write(b"go\n")
        child.stdin.flush()

        grandchild_pid, _ = _wait_for_sentinel(sentinel, child)
        assert process_is_alive(grandchild_pid)

        group.close()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and process_is_alive(child.pid):
            time.sleep(0.05)
        assert not process_is_alive(child.pid)

        tick_at_close = int(sentinel.read_text(encoding="utf-8").strip().split(":", 1)[1])
        deadline = time.monotonic() + 2.0
        progressed = False
        while time.monotonic() < deadline:
            raw = sentinel.read_text(encoding="utf-8").strip()
            if ":" in raw and int(raw.split(":", 1)[1]) > tick_at_close:
                progressed = True
                break
            time.sleep(0.05)

        assert process_is_alive(grandchild_pid)
        assert progressed
    finally:
        group.close()
        if child is not None and child.poll() is None:
            child.kill()
        _terminate_pid(grandchild_pid)

