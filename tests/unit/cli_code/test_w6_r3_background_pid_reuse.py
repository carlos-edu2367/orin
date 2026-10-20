"""W6-R3: a background run's record names its process by pid and creation time; only a matching process is ever stopped."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentos.cli_code.commands import background

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]


def _kill(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
        process.wait(timeout=10)


def _write_record(run_id: str, pid: int, **extra) -> None:
    root = background.background_root()
    root.mkdir(parents=True, exist_ok=True)
    record = {"id": run_id, "pid": pid, "cwd": ".", "args": ["-p", "x"], "log": str(root / f"{run_id}.log"),
              "session_id": None, "started_at": "2026-01-01T00:00:00+00:00", **extra}
    (root / f"{run_id}.json").write_text(json.dumps(record), encoding="utf-8")


def test_a_reused_pid_is_reported_finished_and_purge_leaves_the_bystander_alive() -> None:
    bystander = subprocess.Popen(SLEEP)
    try:
        _write_record("bg_wrong000000", bystander.pid, created=1)  # the pid was reused: another creation time
        _write_record("bg_missing0000", bystander.pid)  # written before creation times were recorded
        states = {item["id"]: item["state"] for item in background.background_runs()}
        assert states == {"bg_wrong000000": "finished", "bg_missing0000": "finished"}
        assert background.run("purge", ["--yes"], cwd=Path("."), stdout=io.StringIO(), stderr=io.StringIO()) == 0
        time.sleep(0.5)
        assert bystander.poll() is None
    finally:
        _kill(bystander)


def test_a_process_whose_creation_time_matches_is_running_and_stopped() -> None:
    # Started as `start_background` starts a run: on POSIX it leads its own process group, which `_stop` requires.
    child = subprocess.Popen(SLEEP, start_new_session=sys.platform != "win32")
    try:
        created = background.process_created(child.pid)
        assert created is not None
        _write_record("bg_matching0000", child.pid, created=created)
        assert [item["state"] for item in background.background_runs()] == ["running"]
        assert background._stop(background._record("bg_matching0000")) is True
        assert child.wait(timeout=20) != 0
    finally:
        _kill(child)


def test_stop_never_kills_a_mismatched_or_unrecorded_process() -> None:
    bystander = subprocess.Popen(SLEEP)
    try:
        created = background.process_created(bystander.pid)
        assert background._stop({"pid": bystander.pid, "created": None}) is False
        assert background._stop({"pid": bystander.pid, "created": (created or 0) + 1}) is False
        assert background._stop({"pid": 0, "created": created}) is False
        time.sleep(0.2)
        assert bystander.poll() is None
    finally:
        _kill(bystander)


def test_start_records_the_creation_time_of_the_child(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    holder = subprocess.Popen(SLEEP)
    try:
        def fake_popen(command, **kwargs):
            captured["command"] = command
            return holder

        monkeypatch.setattr(background.subprocess, "Popen", fake_popen)
        out = io.StringIO()
        assert background.start_background(["-p", "hi"], cwd=tmp_path, stdout=out) == 0
        run_id = out.getvalue().strip()
        record = background._record(run_id)
        assert record["pid"] == holder.pid
        assert record["created"] == background.process_created(holder.pid)
        assert background._same_process(record) is True
    finally:
        _kill(holder)


@pytest.mark.skipif(sys.platform != "win32" and not Path("/proc/self/stat").exists(), reason="no process start time here")
def test_the_creation_time_of_this_process_is_stable() -> None:
    import os

    first = background.process_created(os.getpid())
    assert first is not None and first == background.process_created(os.getpid())
