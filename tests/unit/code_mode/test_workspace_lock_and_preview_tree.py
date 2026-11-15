from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentos.agentic.tools._shared import (
    _DEV_SERVER_CMD_RE,
    cleanup_session_background_processes,
    register_background_job,
    terminate_background_job,
)
from agentos.agentic.tools.shell.tasks import ShellTaskTools
from agentos.code_mode.preview_server import PreviewServerManager
from agentos.code_mode.workspace_lock import (
    _claim_rewind,
    _live_turn_marker,
    _rewind_holder_alive,
    _write_turn_marker,
    sweep_stale_workspace_locks,
)
from agentos.launcher.processes import ProcessGroup
from agentos.launcher.state import process_is_alive
from agentos.process_identity import process_created


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object test")
def test_nested_process_group_disallows_breakaway_and_kills_grandchildren(tmp_path: Path) -> None:
    group = ProcessGroup(allow_breakaway=False)
    parent: subprocess.Popen[bytes] | None = None
    try:
        marker = tmp_path / "grandchild.pid"
        script = (
            "import subprocess, sys, pathlib, time\n"
            "CREATE_BREAKAWAY_FROM_JOB = 0x01000000\n"
            "try:\n"
            "    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], creationflags=CREATE_BREAKAWAY_FROM_JOB)\n"
            "except OSError:\n"
            "    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            f"pathlib.Path({str(marker)!r}).write_text(str(p.pid))\n"
            "time.sleep(30)\n"
        )
        parent = subprocess.Popen([sys.executable, "-c", script])
        assert group.adopt(parent.pid) is True

        deadline = time.monotonic() + 5.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        grandchild_pid = int(marker.read_text().strip())

        group.terminate_all()
        parent.wait(timeout=5)

        deadline = time.monotonic() + 3.0
        while process_is_alive(grandchild_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert process_is_alive(grandchild_pid) is False
    finally:
        if parent is not None and parent.poll() is None:
            parent.kill()
        group.terminate_all()
        group.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object Popen->adopt race test")
def test_process_group_adopt_captures_already_spawned_descendants(tmp_path: Path) -> None:
    group = ProcessGroup(allow_breakaway=False)
    parent: subprocess.Popen[bytes] | None = None
    try:
        marker = tmp_path / "fast_child.pid"
        script = (
            "import subprocess, sys, pathlib, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            f"pathlib.Path({str(marker)!r}).write_text(str(p.pid))\n"
            "time.sleep(30)\n"
        )
        parent = subprocess.Popen([sys.executable, "-c", script])
        deadline = time.monotonic() + 5.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        fast_child_pid = int(marker.read_text().strip())

        # Adopt AFTER the child was already spawned by parent!
        assert group.adopt(parent.pid) is True
        group.terminate_all()
        parent.wait(timeout=5)

        deadline = time.monotonic() + 3.0
        while process_is_alive(fast_child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert process_is_alive(fast_child_pid) is False
    finally:
        if parent is not None and parent.poll() is None:
            parent.kill()
        group.terminate_all()
        group.close()


def test_python_server_detection_preserves_foreground_scripts_and_recognizes_explicit_bg() -> None:
    # Foreground script `python main.py` or `python run.py` MUST NOT match `_DEV_SERVER_CMD_RE`
    # (otherwise 2s foreground scripts get prematurely auto-backgrounded!)
    assert _DEV_SERVER_CMD_RE.search("python run.py") is None
    assert _DEV_SERVER_CMD_RE.search("python main.py") is None
    assert _DEV_SERVER_CMD_RE.search("python scripts/build.py") is None
    # Explicit server frameworks DO match `_DEV_SERVER_CMD_RE`
    assert _DEV_SERVER_CMD_RE.search("python -m uvicorn app.main:app --port 8000") is not None
    assert _DEV_SERVER_CMD_RE.search("python -m http.server 8080") is not None
    assert _DEV_SERVER_CMD_RE.search("streamlit run app.py") is not None
    assert _DEV_SERVER_CMD_RE.search("gunicorn app:app") is not None

    dummy = type("DummyTasks", (ShellTaskTools,), {"_background_log_text": lambda self, _p: ""})()
    # Explicit background (`auto_backgrounded=False`) treats `python run.py` as long-lived server
    assert dummy._is_long_lived({"command": "python run.py", "auto_backgrounded": False}) is True
    assert dummy._is_long_lived({"command": "python server.py", "auto_backgrounded": False}) is True
    # Auto-backgrounded timeout (`auto_backgrounded=True`) does NOT treat `python run.py` as a server without port output
    assert dummy._is_long_lived({"command": "python run.py", "auto_backgrounded": True}) is False
    assert dummy._is_long_lived({"command": "python -m uvicorn app.main:app --port 8000"}) is True
    assert dummy._is_long_lived({"command": "pytest tests/"}) is False


def test_background_job_registry_and_stop_process_pid_verification(tmp_path: Path) -> None:
    fake_job = MagicMock()
    register_background_job(777777, fake_job)
    assert terminate_background_job(777777) is True
    fake_job.terminate.assert_called_once()
    fake_job.close.assert_called_once()
    assert terminate_background_job(777777) is False

    fake_job_2 = MagicMock()
    register_background_job(888888, fake_job_2)
    assert cleanup_session_background_processes() == 1
    fake_job_2.terminate.assert_called_once()
    fake_job_2.close.assert_called_once()

    # Verify PreviewServerManager.stop_server refuses to kill a reused PID when `created` mismatches
    orin_dir = tmp_path / ".orin"
    orin_dir.mkdir(parents=True, exist_ok=True)
    my_pid = os.getpid()
    real_ct = process_created(my_pid)
    stale_ct = (real_ct - 36_000_000_000) if isinstance(real_ct, int) else f"{real_ct}-stale"
    manifest_file = orin_dir / "preview_servers.json"
    manifest_file.write_text(
        json.dumps(
            {
                "web": {
                    "name": "web",
                    "pid": my_pid,
                    "created": stale_ct,
                    "port": 5173,
                    "status": "running",
                }
            }
        ),
        encoding="utf-8",
    )
    mgr = PreviewServerManager(tmp_path)
    res = mgr.stop_server("web")
    assert res["stopped"] is False
    assert res["status"] == "stopped"


def test_native_turn_closes_non_persistent_cli_handle_before_background_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime, timedelta
    import agentos.agentic.background_wait as bg_wait_mod
    import agentos.agentic.native_turn as native_turn_mod

    events_order: list[str] = []

    class FakeHandle:
        def __init__(self) -> None:
            self.close_calls = 0

        def run_until_result(self, sink) -> object:
            return SimpleNamespace(
                init=SimpleNamespace(session_id=""),
                result=SimpleNamespace(subtype="success", session_id=""),
                final_text="Launched subagent.",
            )

        def close(self) -> None:
            self.close_calls += 1
            events_order.append("handle.close")

    fake_handle = FakeHandle()

    def fake_wait_for_bg(conv_id, toolset, *, timeout, cancelled, extra_wake):
        events_order.append("wait_for_background_work")
        assert fake_handle.close_calls == 1, "CLI handle must be closed BEFORE wait_for_background_work blocks!"

    monkeypatch.setattr(bg_wait_mod, "has_pending_background_work", lambda *a, **k: True)
    monkeypatch.setattr(bg_wait_mod, "waiting_labels", lambda *a, **k: ["subagent-1"])
    monkeypatch.setattr(bg_wait_mod, "wait_for_background_work", fake_wait_for_bg)
    monkeypatch.setattr(bg_wait_mod, "take_background_notifications", lambda *a, **k: "")
    monkeypatch.setattr(native_turn_mod, "ensure_isolated_cwd", lambda _p: None)
    monkeypatch.setattr(native_turn_mod.NativeTurnRunner, "_compact_prior_history", lambda self, _ts: False)
    monkeypatch.setattr(
        native_turn_mod.NativeTurnRunner,
        "_build_initial_user_content",
        lambda self, *a, **k: [{"type": "text", "text": "hi"}],
    )

    now = datetime.now(UTC)
    runtime = MagicMock()
    runtime.store = SimpleNamespace(finish=lambda *a, **k: None, delta=lambda *a, **k: None)
    runtime.clock.return_value = now
    runtime.cancelled.return_value = False
    runtime.limits = SimpleNamespace(
        max_iterations=10,
        max_turns=10,
        max_output_tokens=4096,
        context_window_tokens=128000,
        max_context_tokens=128000,
    )
    runtime.autocompact = None
    runtime._compaction_count = 0
    runtime.effort = None
    runtime._output_cap = None
    runtime._fallback_models = ()
    runtime._names = None
    runtime.phases = None
    runtime._tool_schemas.return_value = []
    runtime._request_messages.return_value = []
    runtime._context_usage.return_value = {"used_tokens": 100}
    runtime._needs_verification.return_value = False
    runtime._is_live_or_friend.return_value = False
    runtime._emit.return_value = None
    runtime._bg_continuations = 0
    runtime._stop_continuations = 0
    runtime._stop_hook_active = False
    runtime._code_verification.return_value = None
    runtime.toolset = MagicMock()

    provider = MagicMock()
    provider.adapter = SimpleNamespace(
        name="antigravity",
        capabilities=SimpleNamespace(
            tool_list_changed=False,
            persistent_stdin=False,
            supports_session_resume=False,
            max_tool_call_seconds=None,
            offload_tool_images=False,
        ),
    )
    provider.start_native_turn.return_value = fake_handle

    turn = {"turn_id": "t_1", "conversation_id": "conv_1", "model_id": "gemini-4-argon-high"}
    runner = native_turn_mod.NativeTurnRunner(
        runtime=runtime,
        provider=provider,
        turn=turn,
        turn_id="t_1",
        messages=[{"role": "user", "content": "hi"}],
        deadline=now + timedelta(minutes=10),
    )
    result = runner.run()
    assert result is not None
    assert result.state == "completed"
    assert events_order == ["handle.close", "wait_for_background_work"]
    assert fake_handle.close_calls == 1


def test_workspace_lock_detects_pid_reuse_and_sweeps_only_turn_and_rewind_markers(tmp_path: Path) -> None:
    my_pid = os.getpid()
    real_ct = process_created(my_pid)
    assert real_ct is not None

    digest = "abc123digest"
    stale_ct = (real_ct - 36_000_000_000) if isinstance(real_ct, int) else f"{real_ct}-stale"
    reused_marker = tmp_path / f"{digest}.turn.{my_pid}.deadbeef"
    reused_marker.write_text(f"{my_pid} {stale_ct}", encoding="utf-8")

    # Even though `my_pid == os.getpid()`, mismatched creation time proves PID reuse -> removed!
    assert _live_turn_marker(tmp_path, digest) is False
    assert not reused_marker.exists()

    # Rewind marker with reused PID is also detected as stale
    reused_rewind = tmp_path / f"{digest}.rewind"
    reused_rewind.write_text(f"{my_pid} {stale_ct}", encoding="utf-8")
    assert _rewind_holder_alive(reused_rewind) is False
    reused_rewind.unlink()

    # Freshly written markers with real creation time ARE live
    real_turn = _write_turn_marker(tmp_path, digest)
    assert _live_turn_marker(tmp_path, digest) is True
    real_turn.unlink()

    real_rewind = _claim_rewind(tmp_path, digest, "key")
    assert _rewind_holder_alive(real_rewind) is True
    real_rewind.unlink()

    # Legacy 1-token PID markers still work
    legacy_live = tmp_path / f"{digest}.turn.{my_pid}.legacy"
    legacy_live.write_text(str(my_pid), encoding="utf-8")
    assert _live_turn_marker(tmp_path, digest) is True

    # Unwritten (empty) marker within _UNWRITTEN_SECONDS is preserved, old empty marker is swept
    fresh_empty = tmp_path / "fresh.rewind"
    fresh_empty.write_text("", encoding="utf-8")
    stale_empty = tmp_path / "stale.rewind"
    stale_empty.write_text("", encoding="utf-8")
    old_mtime = time.time() - 30.0
    os.utime(stale_empty, (old_mtime, old_mtime))

    # Sweep removes dead `.turn.*` and `.rewind`, but NEVER touches unrelated `.lock` files or live markers
    dead_turn = tmp_path / "otherdigest.turn.99999999.cafe"
    dead_turn.write_text("99999999 12345", encoding="utf-8")
    unrelated_flock = tmp_path / "orin.lock"
    unrelated_flock.write_text("99999999", encoding="utf-8")

    removed = sweep_stale_workspace_locks(tmp_path)
    assert removed == 2  # dead_turn + stale_empty
    assert not dead_turn.exists()
    assert not stale_empty.exists()
    assert fresh_empty.exists()
    assert legacy_live.exists()
    assert unrelated_flock.exists()
