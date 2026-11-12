"""Fase 4: waiting for background work without sleep-and-poll."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from agentos.agentic import write_claims
from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tools.registry import default_registry
from agentos.agentic.tools.shell.bash import _leading_sleep, _pure_sleep_seconds
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


def test_leading_sleeps_are_recognised() -> None:
    assert _leading_sleep("sleep 420; cd out && ls", "posix") == (420, "cd out && ls")
    assert _leading_sleep("sleep 2m && cat out.log", "posix") == (120, "cat out.log")
    assert _leading_sleep("Start-Sleep -Seconds 90; Get-Content x.log", "powershell") == (90, "Get-Content x.log")
    assert _leading_sleep("sleep 300", "posix") is None  # a bare sleep is the other path
    assert _leading_sleep("echo a; sleep 300", "posix") is None
    assert _leading_sleep("sleep 300 & echo a", "posix") is None
    assert _leading_sleep("sleep 300; echo a", "cmd") is None


def test_a_leading_sleep_wakes_on_a_background_job_and_then_runs_the_rest(tmp_path: Path, monkeypatch) -> None:
    from agentos import host_shell
    from agentos.agentic.tools.shell import bash as bash_module

    toolset = _toolset(tmp_path)
    pid = _background(toolset, "import time; time.sleep(0.8); print('render ok', flush=True)", "render")
    posix = host_shell.current_host_shell()
    if posix.dialect != "posix":
        import pytest

        pytest.skip("needs a POSIX shell")
    monkeypatch.setattr(bash_module, "current_host_shell", lambda: posix)
    began = time.monotonic()
    outcome = toolset.invoke("run_command", {"command": f"sleep 300; {PY} -c \"print('checked')\"", "timeout_seconds": 600})
    assert time.monotonic() - began < 20
    assert outcome.status == "succeeded", outcome.content
    assert "leading sleep 300" in outcome.content and "because background work finished" in outcome.content
    assert f"Background process {pid} (render) finished (exit 0)" in outcome.content
    assert "checked" in outcome.content
    assert outcome.payload["woke_early"] is True


def test_a_command_does_not_claim_files_another_conversation_wrote_meanwhile(tmp_path: Path) -> None:
    write_claims.reset()
    root = tmp_path / "shared"
    root.mkdir()
    mine = AgentToolset(ConversationWorkspace.at_root(str(root)), code_mode_active=True, conversation_id="chat-stock")
    other = AgentToolset(ConversationWorkspace.at_root(str(root)), code_mode_active=True, conversation_id="chat-videos")
    # While this chat's command runs, the other chat writes a file in the same folder.
    script = (
        "import pathlib,time; time.sleep(2.5); pathlib.Path('mine.txt').write_text('a')"
    )
    import threading

    def other_writes() -> None:
        time.sleep(0.8)
        written = other.invoke("write_file", {"path": "docs/brief.md", "content": "brief"})
        assert written.status == "succeeded", written.content

    writer = threading.Thread(target=other_writes)
    writer.start()
    outcome = mine.invoke("run_command", {"command": f'{PY} -c "{script}"', "timeout_seconds": 60})
    writer.join()
    assert outcome.status == "succeeded", outcome.content
    paths = {item["path"] for item in outcome.payload.get("artifacts") or []}
    assert "mine.txt" in paths
    assert "docs/brief.md" not in paths
    write_claims.reset()


def test_a_long_sleep_with_nothing_to_wait_for_stops_at_its_timeout(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    began = time.monotonic()
    outcome = toolset.invoke("run_command", {"command": "sleep 90", "timeout_seconds": 1})
    assert 0.8 <= time.monotonic() - began < 5
    assert outcome.status == "succeeded" and "Waited 1s of 90s" in outcome.content and "block=true" in outcome.content


def _fast_auto_background(monkeypatch) -> None:
    from agentos.agentic.tools.shell import bash as bash_module

    monkeypatch.setattr(bash_module, "FOREGROUND_AUTO_BACKGROUND_SECONDS", 1)


def test_a_slow_foreground_command_moves_to_the_background_and_is_announced_when_it_ends(tmp_path: Path, monkeypatch) -> None:
    _fast_auto_background(monkeypatch)
    toolset = _toolset(tmp_path)
    code = "import sys,time; print('step 1', flush=True); time.sleep(2.5); print('done 2', flush=True); sys.exit(3)"
    began = time.monotonic()
    outcome = toolset.invoke("run_command", {"command": f'{PY} -c "{code}"', "timeout_seconds": 60, "description": "slow job"})
    assert time.monotonic() - began < 6
    assert outcome.status == "succeeded", outcome.content
    pid = outcome.payload["pid"]
    assert outcome.payload["backgrounded_by_time"] is True
    assert "Your command is taking a while" in outcome.content
    assert "now running in the background" in outcome.content
    assert f"Its id is pid {pid}" in outcome.content
    assert "You will be notified when it finishes" in outcome.content
    assert [row["pid"] for row in toolset.awaited_background_processes()] == [pid]  # the turn waits for it
    deadline = time.monotonic() + 15
    while toolset.awaited_background_processes() and time.monotonic() < deadline:
        time.sleep(0.2)
    notice = toolset.take_finished_process_notices()
    assert f"Background process {pid} (slow job) failed (exit 3)" in notice and "done 2" in notice


def test_a_backgrounded_command_is_stopped_at_its_timeout_and_the_model_is_told(tmp_path: Path, monkeypatch) -> None:
    _fast_auto_background(monkeypatch)
    toolset = _toolset(tmp_path)
    code = "import time; print('hung', flush=True); time.sleep(60)"
    outcome = toolset.invoke("run_command", {"command": f'{PY} -c "{code}"', "timeout_seconds": 3, "description": "hung"})
    assert outcome.status == "succeeded" and outcome.payload["backgrounded_by_time"] is True, outcome.content
    pid = outcome.payload["pid"]
    deadline = time.monotonic() + 20
    while toolset.awaited_background_processes() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not toolset.awaited_background_processes()
    notice = toolset.take_finished_process_notices()
    assert f"Background process {pid} (hung) was stopped at its 3s timeout" in notice


def test_a_quick_command_and_an_opted_out_one_stay_in_the_foreground(tmp_path: Path, monkeypatch) -> None:
    _fast_auto_background(monkeypatch)
    toolset = _toolset(tmp_path)
    quick = toolset.invoke("run_command", {"command": f'{PY} -c "print(1)"'})
    assert quick.status == "succeeded" and not quick.payload.get("background")
    slow = toolset.run_command(f'{PY} -c "import time; time.sleep(2); print(42)"', timeout_seconds=30, auto_background=False)
    assert not slow.get("background") and "42" in str(slow.get("content"))
