from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from agentos.installation.paths import OrinPaths
from agentos.launcher.state import (
    InstanceLock,
    InstanceState,
    clear_state,
    lock_is_free,
    process_is_alive,
    read_state,
    request_stop,
    running_instance,
    stop_requested,
    write_state,
)


def _paths(tmp_path: Path) -> OrinPaths:
    return OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()


def test_a_held_lock_blocks_a_second_launcher(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    first = InstanceLock(paths.instance_lock)

    assert first.acquire() is True
    assert lock_is_free(paths) is False
    assert InstanceLock(paths.instance_lock).acquire() is False

    first.release()
    assert lock_is_free(paths) is True


def test_a_lock_is_released_when_the_holding_process_dies(tmp_path: Path) -> None:
    # This is the property a pid file cannot provide: a crashed launcher leaves
    # nothing behind that a later run has to guess about.
    paths = _paths(tmp_path)
    import agentos

    source_root = str(Path(agentos.__file__).resolve().parent.parent)
    script = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {source_root!r})
        from agentos.launcher.state import InstanceLock
        lock = InstanceLock({str(paths.instance_lock)!r})
        assert lock.acquire()
        print("locked", flush=True)
        time.sleep(30)
        """
    )
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        assert lock_is_free(paths) is False
    finally:
        child.kill()
        child.wait(timeout=10)

    # Windows can keep a dying process's file lock for a moment after the
    # process object is reaped, so the property under test is that the lock
    # becomes free on its own, not that it is free on the very next instruction.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not lock_is_free(paths):
        time.sleep(0.05)
    assert lock_is_free(paths) is True


def test_a_stale_state_file_without_a_lock_is_not_an_instance(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    write_state(paths, InstanceState(pid=999_999, port=8000, url="http://127.0.0.1:8000", version="1", profile="development", started_at="now"))

    assert running_instance(paths) is None
    assert not paths.instance_state.exists()


def test_state_survives_a_round_trip(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    state = InstanceState.create(port=8123, url="http://127.0.0.1:8123", version="0.1.0", profile="development")

    write_state(paths, state)

    assert read_state(paths) == state
    assert json.loads(paths.instance_state.read_text(encoding="utf-8"))["port"] == 8123


def test_unreadable_state_is_reported_as_absent(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.instance_state.write_text("{not json", encoding="utf-8")

    assert read_state(paths) is None


def test_stop_requests_are_written_and_cleared(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    assert stop_requested(paths) is False
    request_stop(paths)
    assert stop_requested(paths) is True

    clear_state(paths)
    assert stop_requested(paths) is False


def test_liveness_distinguishes_this_process_from_a_dead_one(tmp_path: Path) -> None:
    assert process_is_alive(os.getpid()) is True

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)

    assert process_is_alive(child.pid) is False or child.pid == os.getpid()
