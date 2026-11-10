"""Fase 4: waiting for background work without sleep-and-poll."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tools.registry import default_registry
from agentos.agentic.tools.shell.bash import _pure_sleep_seconds
from agentos.agentic.workspace import ConversationWorkspace

PY = f'"{sys.executable}"'


def _toolset(tmp_path: Path) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path / "w", "c-wait"), code_mode_active=True)


def _background(toolset: AgentToolset, code: str, description: str = "") -> int:
    started = toolset.invoke("run_command", {"command": f'{PY} -c "{code}"', "background": True, "description": description})
    assert started.status == "succeeded", started.content
    return int(started.payload["pid"])


def test_the_legacy_schema_offers_the_wait() -> None:
    properties = default_registry().get("read_process_output").legacy_schema["properties"]
    assert {"block", "wait_seconds", "until_pattern"} <= set(properties)
    assert properties["wait_seconds"]["maximum"] == 600


def test_until_pattern_returns_when_the_log_matches(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    pid = _background(toolset, "import time; print('booting', flush=True); time.sleep(0.5); print('READY 1', flush=True); time.sleep(30)")
    try:
        began = time.monotonic()
        outcome = toolset.invoke("read_process_output", {"pid": pid, "until_pattern": r"READY \d", "wait_seconds": 20})
        assert time.monotonic() - began < 10
        assert outcome.status == "succeeded", outcome.content
        assert "status=running until_pattern=matched" in outcome.content and "READY 1" in outcome.content
        assert outcome.payload["pattern_matched"] is True
    finally:
        toolset.stop_process(pid)


def test_a_blocking_read_reports_the_exit_code_once(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    pid = _background(toolset, "import sys,time; time.sleep(0.3); print('half', flush=True); sys.exit(4)", "render")
    outcome = toolset.invoke("read_process_output", {"pid": pid, "block": True, "wait_seconds": 20})
    assert f"pid={pid} status=exited exit=4" in outcome.content and outcome.payload["exit_code"] == 4
    assert toolset.take_finished_process_notices() == ""  # the model saw it end; nobody tells it again


def test_a_bad_pattern_is_a_clear_error(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    pid = _background(toolset, "import time; time.sleep(5)")
    try:
        outcome = toolset.invoke("read_process_output", {"pid": pid, "until_pattern": "(unclosed"})
        assert outcome.status == "failed" and "until_pattern" in outcome.content
    finally:
        toolset.stop_process(pid)


def test_another_jobs_exit_is_announced_on_a_read(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    quick = _background(toolset, "print('quick done')", "quick")
    slow = _background(toolset, "import time; time.sleep(30)", "slow")
    try:
        time.sleep(1.0)
        outcome = toolset.invoke("read_process_output", {"pid": slow})
        assert f"Background process {quick}" in outcome.content and "has exited with code 0" in outcome.content
    finally:
        toolset.stop_process(slow)


def test_servers_and_watchers_are_not_awaited(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    job = _background(toolset, "import time; time.sleep(30)", "render")
    try:
        class Running:
            def poll(self):
                return None

        for index, command in enumerate(("npm run dev", "tsc --watch", "uvicorn app:api")):
            fake_pid = 990_000 + index
            toolset._background_shells[f"bash_{fake_pid}"] = {"pid": fake_pid, "command": command, "process": Running(), "status": "running"}
            toolset._turn_background_pids.add(fake_pid)
        assert [row["pid"] for row in toolset.awaited_background_processes()] == [job]
    finally:
        toolset.stop_process(job)


def test_pure_sleeps_are_recognised() -> None:
    assert _pure_sleep_seconds("sleep 300") == 300
    assert _pure_sleep_seconds("sleep 5m") == 300
    assert _pure_sleep_seconds("Start-Sleep -Seconds 90") == 90
    assert _pure_sleep_seconds("timeout /t 120 /nobreak > nul") == 120
    assert _pure_sleep_seconds("sleep 300 && cat out.log") is None
    assert _pure_sleep_seconds("python sleep.py 300") is None


def test_a_long_sleep_wakes_when_a_background_job_ends(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    pid = _background(toolset, "import time,sys; time.sleep(0.8); print('TTS 10/10', flush=True); sys.exit(1)", "tts")
    began = time.monotonic()
    outcome = toolset.invoke("run_command", {"command": "sleep 300", "timeout_seconds": 600})
    assert time.monotonic() - began < 15
    assert outcome.status == "succeeded", outcome.content
    assert "because background work finished" in outcome.content
    assert f"Background process {pid} (tts) failed (exit 1)" in outcome.content and "TTS 10/10" in outcome.content
    assert outcome.payload["woke_early"] is True


def test_a_long_sleep_with_nothing_to_wait_for_stops_at_its_timeout(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    began = time.monotonic()
    outcome = toolset.invoke("run_command", {"command": "sleep 90", "timeout_seconds": 1})
    assert 0.8 <= time.monotonic() - began < 5
    assert outcome.status == "succeeded" and "Waited 1s of 90s" in outcome.content and "block=true" in outcome.content
