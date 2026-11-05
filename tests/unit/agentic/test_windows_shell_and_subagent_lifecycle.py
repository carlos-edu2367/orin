from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.session import TurnSession
from agentos.agentic.tools._shared import (
    MAX_COMMAND_TIMEOUT_SECONDS,
    AgentToolError,
    _terminate_process_tree,
    coerce_tool_arguments,
)
from agentos.agentic.tools.shell.bash import BashTools
from agentos.code_mode.checkpoints import FileCheckpointStore


def test_coerce_tool_arguments_converts_ms_timeout_to_seconds_and_preserves_seconds() -> None:
    coerced = coerce_tool_arguments("powershell", {"command": "Write-Output ok", "timeout": 120000})
    assert coerced["timeout_seconds"] == 120
    coerced_dict, _ = coerced
    assert coerced_dict["timeout_seconds"] == 120

    coerced_run, _ = coerce_tool_arguments("run_command", {"command": "echo ok", "timeout": 5000})
    assert coerced_run["timeout_seconds"] == 5

    coerced_bash, _ = coerce_tool_arguments("bash", {"command": "echo ok", "timeout": 15000})
    assert coerced_bash["timeout_seconds"] == 15

    coerced_sec, _ = coerce_tool_arguments("powershell", {"command": "Write-Output ok", "timeout_seconds": 120})
    assert coerced_sec["timeout_seconds"] == 120

    # Adversarial cases: huge ms timeout clamps to MAX_COMMAND_TIMEOUT_SECONDS, sub-second ms clamps to 1s
    coerced_huge, _ = coerce_tool_arguments("powershell", {"command": "Write-Output ok", "timeout": 99_000_000})
    assert coerced_huge["timeout_seconds"] == MAX_COMMAND_TIMEOUT_SECONDS

    coerced_small_ms, _ = coerce_tool_arguments("powershell", {"command": "Write-Output ok", "timeout": 250})
    assert coerced_small_ms["timeout_seconds"] >= 1


def test_terminate_process_tree_kills_orphan_tree_on_windows_even_after_parent_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed_pids: list[int] = []
    monkeypatch.setattr("agentos.agentic.tools._shared.os.name", "nt", raising=False)
    monkeypatch.setattr("agentos.agentic.tools._shared._terminate_pid", lambda pid: killed_pids.append(pid))

    fake_proc = MagicMock()
    fake_proc.pid = 4242
    # Parent process already exited (poll() == 0), but grandchild processes in the tree still hold pipes
    fake_proc.poll.return_value = 0
    fake_proc.stdout = MagicMock()
    fake_proc.stderr = MagicMock()

    _terminate_process_tree(fake_proc)

    assert killed_pids == [4242]
    fake_proc.stdout.close.assert_called_once()
    fake_proc.stderr.close.assert_called_once()


class _DummyShellRunner(BashTools):
    def __init__(self, workspace_root: Path) -> None:
        self._current_cwd = workspace_root
        self._extra_env: dict[str, str] = {}
        self._on_tool_progress = lambda *_args, **_kwargs: None
        self._active_invocation_id = "inv-test"
        self._read_file_state: dict[str, Any] = {}
        self.workspace = MagicMock()
        self.workspace.root = workspace_root
        self.workspace.additional_roots = ()
        self.workspace.begin_change_tracking.return_value = {}
        self.workspace.end_change_tracking.return_value = []
        self.workspace.key.return_value = "."

    def _cancel_requested(self) -> bool:
        return False

    def _persist_truncated_output(self, tool_name: str, full_body: str) -> str | None:
        return None

    def _queue_reindex(self, artifacts: Any) -> None:
        pass

    def _record_file_read(self, path: str, *, explicit_read: bool = True) -> None:
        pass


def test_run_shell_releases_pipes_when_parent_exits_with_grandchild_holding_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _DummyShellRunner(tmp_path)

    class _BlockingPipe:
        def __init__(self, initial_Chunk: bytes = b"") -> None:
            self._initial = initial_Chunk
            self._closed = threading.Event()

        def read(self, _size: int = 1024) -> bytes:
            if self._initial:
                chunk = self._initial
                self._initial = b""
                return chunk
            # Simulate a Windows grandchild process keeping the pipe handle open for 2.5s unless closed
            self._closed.wait(timeout=2.5)
            return b""

        def readline(self, _size: int = -1) -> bytes:
            return self.read(_size)

        def close(self) -> None:
            self._closed.set()

    class _FakePopen:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.pid = 9876
            self.returncode = 0
            self.stdout = _BlockingPipe(b"parent output finished\n")
            self.stderr = _BlockingPipe(b"")
            self._started = time.monotonic()

        def poll(self) -> int | None:
            if (time.monotonic() - self._started) >= 0.05:
                return 0
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            raise AssertionError("communicate() must not be called when streaming threads are used")

    monkeypatch.setattr("agentos.agentic.tools.shell.bash.subprocess.Popen", _FakePopen)

    t0 = time.monotonic()
    result = runner._run_shell(
        "powershell -Command Write-Output ok",
        timeout_seconds=30,
        tool_name="powershell",
        description="Run quick command",
    )
    elapsed = time.monotonic() - t0

    assert elapsed < 1.5, f"_run_shell hung for {elapsed:.2f}s waiting on orphan pipe"
    assert "parent output finished" in result["content"]


def test_run_shell_timeout_terminates_tree_without_communicate_hang(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _DummyShellRunner(tmp_path)
    terminated: list[Any] = []
    communicate_calls: list[float | None] = []

    class _StuckPipe:
        def __init__(self) -> None:
            self._closed = threading.Event()

        def read(self, _size: int = 1024) -> bytes:
            self._closed.wait(timeout=2.5)
            return b""

        def readline(self, _size: int = -1) -> bytes:
            return self.read(_size)

        def close(self) -> None:
            self._closed.set()

    class _HangingPopen:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.pid = 5555
            self.returncode = None
            self.stdout = _StuckPipe()
            self.stderr = _StuckPipe()

        def poll(self) -> int | None:
            return None

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            communicate_calls.append(timeout)
            return b"", b""

    def _fake_terminate(proc: Any) -> None:
        terminated.append(proc.pid)
        proc.stdout.close()
        proc.stderr.close()

    monkeypatch.setattr("agentos.agentic.tools.shell.bash.subprocess.Popen", _HangingPopen)
    monkeypatch.setattr("agentos.agentic.tools.shell.bash._terminate_process_tree", _fake_terminate)

    with pytest.raises(AgentToolError, match="timed out"):
        runner._run_shell(
            "powershell -Command Start-Sleep 999",
            timeout_seconds=1,
            tool_name="powershell",
        )
    assert terminated == [5555]
    assert communicate_calls == []


class _RecordingStore:
    def __init__(self) -> None:
        self.events: list[tuple[AgentActivityEventType, str, dict[str, object], str | None, str | None]] = []

    def record(
        self,
        turn: dict[str, object],
        event_type: AgentActivityEventType,
        summary: str,
        payload: dict[str, object],
        *,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
    ) -> None:
        self.events.append((event_type, summary, dict(payload), agent_id, parent_agent_id))


class _RecordingAgentsStore:
    def __init__(self) -> None:
        self.states: list[tuple[str, str]] = []

    def set_state(self, agent_id: str, state: str) -> None:
        self.states.append((agent_id, state))

    def record_usage(self, *args: Any, **kwargs: Any) -> None:
        pass


def _make_minimal_session(tmp_path: Path) -> tuple[TurnSession, _RecordingStore, _RecordingAgentsStore]:
    store = _RecordingStore()
    agents_store = _RecordingAgentsStore()
    session = object.__new__(TurnSession)
    session.turn = {
        "turn_id": "turn-1",
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-6",
        "code_mode": "code",
    }
    session.store = store
    session.agents_store = agents_store
    session.main_agent_id = "agent:main"
    session.cli_options = None
    session._code_mode_stage = None
    session.cancelled = lambda _turn: False
    session.lifecycle = None
    session.hook_engine = None
    session.limits = MagicMock()
    session.limits.max_iterations = 8
    session.limits.max_actions = 16
    session.limits.deadline = 60.0
    session.limits.max_context_tokens = 100_000
    session._subagent_lock = threading.Lock()
    session._subagent_histories = {}
    session._subagent_semaphore = threading.Semaphore(2)
    session._background_subagents = {}
    session._bg_subagent_executor = None
    return session, store, agents_store


def test_session_started_summary_for_powershell_and_progress_preserves_description(tmp_path: Path) -> None:
    session, store, _ = _make_minimal_session(tmp_path)

    session.emit_lifecycle(
        session.turn,
        "tool_started",
        tool_name="powershell",
        invocation_id="inv-ps-1",
        command="Get-ChildItem -Force",
        description="Listar arquivos com PowerShell",
    )
    started_events = [e for e in store.events if e[0] == AgentActivityEventType.TOOL_STARTED]
    assert len(started_events) == 1
    _, summary, payload, _, _ = started_events[0]
    assert "Listar arquivos com PowerShell" in summary
    assert payload["command"] == "Get-ChildItem -Force"

    session.emit_lifecycle(
        session.turn,
        "tool_progressed",
        tool_name="powershell",
        invocation_id="inv-ps-1",
        command="Get-ChildItem -Force",
        description="Listar arquivos com PowerShell",
        output_tail="Mode LastWriteTime",
        elapsed_ms=1500,
    )
    progressed_events = [e for e in store.events if e[0] == AgentActivityEventType.TOOL_PROGRESSED]
    assert len(progressed_events) == 1
    _, _, prog_payload, _, _ = progressed_events[0]
    assert prog_payload["description"] == "Listar arquivos com PowerShell"
    assert prog_payload["output_tail"] == "Mode LastWriteTime"


def test_subagent_lifecycle_sets_failed_state_in_finally_on_unhandled_exception_and_cancels_background(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, store, agents_store = _make_minimal_session(tmp_path)
    session._child_provider_factory = lambda _model_id: MagicMock()
    session.provider_factory = lambda: MagicMock()
    session._subagent_task = lambda _rec, text: text
    session._subagent_prompt = lambda _rec, text, _tools: text
    session._subagent_limits = lambda **_kw: MagicMock()
    session._subagent_accounting = lambda *_a, **_kw: {}
    session._child_cancelled = lambda _ev: (lambda _t: False)
    session._definition_effort = lambda _def, _m: None

    fake_toolset = MagicMock()
    fake_toolset.definitions.return_value = []
    session._toolset = lambda **_kw: fake_toolset
    session.permission_mode = "accept_edits"
    session._non_interactive_ask = lambda: "allow"
    session._reminders = MagicMock()
    session.effective_settings = MagicMock()
    session.effective_settings.experimental.return_value = False
    session._subagent_reminders = lambda **_kw: MagicMock()

    def _exploding_run(self: Any, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("unhandled provider crash inside subagent")

    monkeypatch.setattr(AgenticTurnRuntime, "run", _exploding_run)

    with pytest.raises(RuntimeError, match="unhandled provider crash"):
        session._execute_subagent_run_unlocked(
            record={"agent_id": "agent:sub-1", "name": "Explore"},
            agent_id="agent:sub-1",
            clean="Explore",
            builtin_key="explore",
            builtin_spec={"read_only": True},
            child_model_id="claude-sonnet-4-6",
            current_model_id="claude-sonnet-4-6",
            task_text="Inspect repository structure",
            description="Inspect repo",
        )

    assert ("agent:sub-1", "working") in agents_store.states
    assert agents_store.states[-1] == ("agent:sub-1", "failed")
    delegation_failed = [e for e in store.events if e[0] == AgentActivityEventType.DELEGATION_FAILED]
    assert len(delegation_failed) == 1
    assert delegation_failed[0][3] == "agent:sub-1"
    assert delegation_failed[0][4] == "agent:main"

    # Also verify subagent "running" lifecycle event records parent_agent_id=main_agent_id
    session.emit_lifecycle(session.turn, "running", agent_id="agent:sub-1", agent_name="Explore")
    sub_started = [
        e for e in store.events if e[0] == AgentActivityEventType.TURN_STARTED and e[3] == "agent:sub-1"
    ]
    assert len(sub_started) == 1
    assert sub_started[0][4] == "agent:main"

    # Verify close_background_subagents signals entry["cancel"].set() when parent turn failed/cancelled
    cancel_event = threading.Event()
    fut: Future[Any] = Future()
    session._background_subagents["agent:bg-1"] = {
        "future": fut,
        "cancel": cancel_event,
        "name": "BackgroundWorker",
    }
    session.turn["state"] = "failed"
    session.close_background_subagents()
    assert cancel_event.is_set() is True


def test_microcompact_keep_recent_is_16() -> None:
    from agentos.agentic.compaction import MICROCOMPACT_MARKER
    from agentos.agentic.runtime import MICROCOMPACT_KEEP_RECENT, AgenticLimits

    assert MICROCOMPACT_KEEP_RECENT == 16

    class _DummyStore:
        def record_compact_boundary(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    runtime = AgenticTurnRuntime(
        store=_DummyStore(),
        provider=object(),
        limits=AgenticLimits(max_context_tokens=4_000),
    )
    messages: list[dict[str, object]] = [{"role": "user", "content": "task"}]
    for index in range(20):
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": f"c{index}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
        })
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": f"r{index} " + "x" * 500})
    runtime._pinned_index = 0
    runtime._maybe_compact(messages, {"turn_id": "t-mc"}, [])
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert [m["content"] for m in tool_msgs[:4]] == [MICROCOMPACT_MARKER] * 4
    assert all(m["content"] != MICROCOMPACT_MARKER for m in tool_msgs[4:])


def test_file_checkpoint_store_skips_files_larger_than_max_checkpoint_file_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.code_mode.checkpoints import MAX_CHECKPOINT_FILE_BYTES

    assert MAX_CHECKPOINT_FILE_BYTES == 10 * 1024 * 1024

    store = FileCheckpointStore(tmp_path)
    small_file = tmp_path / "small.txt"
    small_file.write_text("hello world", encoding="utf-8")

    small_entry = store.snapshot_before_write("turn-1", "small.txt")
    assert small_entry is not None
    assert small_entry["existed"] is True
    assert small_entry["size_bytes"] == 11

    large_file = tmp_path / "huge.bin"
    large_file.write_bytes(b"x")
    orig_stat = Path.stat

    def _fake_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        st = orig_stat(self, *args, **kwargs)
        if self.name == "huge.bin":
            mock_st = MagicMock(wraps=st)
            mock_st.st_size = MAX_CHECKPOINT_FILE_BYTES + 1
            mock_st.st_mode = st.st_mode
            return mock_st
        return st

    monkeypatch.setattr(Path, "stat", _fake_stat)
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (_ for _ in ()).throw(AssertionError("read_bytes must not be called for >10MB file"))
        if self.name == "huge.bin"
        else b"x",
    )

    large_entry = store.snapshot_before_write("turn-1", "huge.bin")
    assert large_entry is None


def _edit(session: Any, inv: str, status: str) -> None:
    session.emit_lifecycle(session.turn, "tool_started", tool_name="edit_file", invocation_id=inv, path="a.py")
    session.emit_lifecycle(session.turn, "tool_finished", tool_name="edit_file", invocation_id=inv, status=status,
                           summary="edit", tool_payload={}, tool_arguments={"path": "a.py"})


def _pytest(session: Any, inv: str, failed_tests: list[str]) -> None:
    failed = bool(failed_tests)
    content = "exit=1\n" + "\n".join(f"FAILED tests/test_x.py::{t} - AssertionError" for t in failed_tests) if failed else "exit=0\n1 passed"
    session.emit_lifecycle(session.turn, "tool_started", tool_name="run_command", invocation_id=inv, command="pytest -q")
    session.emit_lifecycle(session.turn, "tool_finished", tool_name="run_command", invocation_id=inv, status="succeeded",
                           summary="$ pytest -q", tool_payload={"command": "pytest -q", "failed": failed, "exit_code": 1 if failed else 0},
                           tool_arguments={"command": "pytest -q"}, tool_content=content)


def test_a_failed_edit_does_not_enter_implementing(tmp_path: Path) -> None:
    session, _store, _ = _make_minimal_session(tmp_path)
    session._code_mode_stage = "validating_done"
    _edit(session, "e1", "failed")
    assert session._code_mode_stage == "validating_done"
    _edit(session, "e2", "succeeded")
    assert session._code_mode_stage == "implementing"


def test_a_failure_that_existed_before_any_edit_is_not_fixing(tmp_path: Path) -> None:
    session, store, _ = _make_minimal_session(tmp_path)
    _pytest(session, "v1", ["test_old"])
    assert session._code_mode_stage != "fixing"
    _edit(session, "e1", "succeeded")
    _pytest(session, "v2", ["test_old"])
    assert session._code_mode_stage != "fixing"
    assert store.events[-1][2].get("preexisting") is True


def test_a_new_failure_after_an_edit_is_fixing(tmp_path: Path) -> None:
    session, _store, _ = _make_minimal_session(tmp_path)
    _pytest(session, "v1", ["test_old"])
    _edit(session, "e1", "succeeded")
    _pytest(session, "v2", ["test_old", "test_new"])
    assert session._code_mode_stage == "fixing"


def test_a_failure_with_no_reference_before_the_edit_is_fixing(tmp_path: Path) -> None:
    session, _store, _ = _make_minimal_session(tmp_path)
    _edit(session, "e1", "succeeded")
    _pytest(session, "v1", ["test_a"])
    assert session._code_mode_stage == "fixing"
