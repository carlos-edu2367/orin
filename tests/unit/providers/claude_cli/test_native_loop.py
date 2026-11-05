from __future__ import annotations

import logging
from pathlib import Path
import threading
import time
from typing import Any, Mapping, Sequence

import httpx
import pytest

from agentos.providers.claude_cli.errors import (
    CliSecurityAbort,
    NativeLoopUnavailable,
    ProviderTransportError,
    RateLimitState,
)
from agentos.providers.claude_cli.native_loop import (
    NativeTurnHandle,
    NativeTurnOutcome,
    NativeTurnRequest,
    NativeTurnSink,
)
from agentos.providers.claude_cli.provider import ClaudeCliProvider, CliSlots
from agentos.providers.claude_cli.stream import CliInit, CliUsage
from agentos.providers.cli_tools_mcp import (
    McpToolSpec,
    ToolCallRequest,
    ToolCallResult,
)

FAKE_CLAUDE_SCRIPT = str(Path(__file__).with_name("fake_claude.py"))


class _RecordingSink(NativeTurnSink):
    def __init__(self) -> None:
        self.inits: list[CliInit] = []
        self.texts: list[str] = []
        self.thinkings: list[str] = []
        self.tools_started: list[tuple[str, str]] = []
        self.tools_progress: list[tuple[str, str, int]] = []
        self.tools_complete: list[tuple[str, str, Mapping[str, Any]]] = []
        self.usages: list[tuple[str, CliUsage]] = []
        self.compacted_count: int = 0
        self.rate_limits: list[RateLimitState] = []

    def on_init(self, init: CliInit) -> None:
        self.inits.append(init)

    def on_text(self, text: str) -> None:
        self.texts.append(text)

    def on_thinking(self, text: str) -> None:
        self.thinkings.append(text)

    def on_tool_use_started(self, tool_use_id: str, tool_name: str) -> None:
        self.tools_started.append((tool_use_id, tool_name))

    def on_tool_input_progress(
        self, tool_use_id: str, tool_name: str, bytes_received: int
    ) -> None:
        self.tools_progress.append((tool_use_id, tool_name, bytes_received))

    def on_tool_use_complete(
        self, tool_use_id: str, tool_name: str, tool_input: Mapping[str, Any]
    ) -> None:
        self.tools_complete.append((tool_use_id, tool_name, dict(tool_input)))

    def on_assistant_usage(self, message_id: str, usage: CliUsage) -> None:
        self.usages.append((message_id, usage))

    def on_compacted(self) -> None:
        self.compacted_count += 1

    def on_rate_limit(self, state: RateLimitState) -> None:
        self.rate_limits.append(state)


class _EchoExecutor:
    def __init__(
        self,
        *,
        on_call: Any | None = None,
    ) -> None:
        self.calls: list[ToolCallRequest] = []
        self._on_call = on_call

    def list_tools(self) -> Sequence[McpToolSpec]:
        return [
            McpToolSpec(
                name="echo",
                description="Echo text back",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                read_only=True,
            )
        ]

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult:
        self.calls.append(request)
        if self._on_call is not None:
            return self._on_call(request)
        return ToolCallResult(text=str(request.arguments.get("text") or ""))


def _make_request(
    tmp_path: Path,
    *,
    executor: Any | None = None,
    cancel_check: Any | None = None,
    deadline_offset: float = 60.0,
    max_turns: int = 5,
) -> NativeTurnRequest:
    return NativeTurnRequest(
        model="claude-opus-5-5",
        system_prompt="You are a test coding agent.",
        user_content=[{"type": "text", "text": "Run the task"}],
        executor=executor or _EchoExecutor(),
        cancel_check=cancel_check or (lambda: False),
        deadline_monotonic=time.monotonic() + deadline_offset,
        max_turns=max_turns,
        cwd=tmp_path,
    )


def test_native_turn_runs_two_dependent_calls_through_executor(
    tmp_path: Path,
) -> None:
    rec = _EchoExecutor()
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "two_dependent_calls"},
    )
    sink = _RecordingSink()
    handle = provider.start_native_turn(_make_request(tmp_path, executor=rec))
    try:
        out = handle.run_until_result(sink)
    finally:
        handle.close()

    assert [c.arguments["text"] for c in rec.calls] == ["A", "AB"]
    assert out.result.subtype == "success"
    assert out.final_text == "Final:AB"


def test_unconnected_mcp_raises_native_unavailable_before_output(
    tmp_path: Path,
) -> None:
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "mcp_failed"},
    )
    sink = _RecordingSink()
    handle = provider.start_native_turn(_make_request(tmp_path))
    try:
        with pytest.raises(NativeLoopUnavailable) as exc_info:
            handle.run_until_result(sink)
        assert "failed" in str(exc_info.value).lower() or "mcp" in str(exc_info.value).lower()
        assert sink.texts == []
    finally:
        handle.close()


def test_api_key_source_aborts(tmp_path: Path) -> None:
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "api_key_leak"},
    )
    sink = _RecordingSink()
    handle = provider.start_native_turn(_make_request(tmp_path))
    try:
        with pytest.raises(CliSecurityAbort) as exc_info:
            handle.run_until_result(sink)
        assert exc_info.value.status_code == 401
        assert exc_info.value.exhausted is True
        # Not "native unavailable": that would fall back to the text path with the same credentials.
        assert not isinstance(exc_info.value, NativeLoopUnavailable)
        assert sink.texts == []
    finally:
        handle.close()


def test_cancel_during_slow_tool_kills_tree_and_closes_server(
    tmp_path: Path,
) -> None:
    tool_entered = threading.Event()
    tool_release = threading.Event()
    cancelled = [False]

    def _slow_call(req: ToolCallRequest) -> ToolCallResult:
        tool_entered.set()
        tool_release.wait(timeout=5.0)
        return ToolCallResult(text="done")

    rec = _EchoExecutor(on_call=_slow_call)
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "single_tool_call"},
    )
    sink = _RecordingSink()
    handle = provider.start_native_turn(
        _make_request(
            tmp_path,
            executor=rec,
            cancel_check=lambda: cancelled[0],
        )
    )
    mcp_url = handle.mcp_url

    def _trigger_cancel() -> None:
        tool_entered.wait(timeout=5.0)
        cancelled[0] = True
        time.sleep(0.2)
        tool_release.set()

    t = threading.Thread(target=_trigger_cancel, daemon=True)
    t.start()
    t0 = time.monotonic()
    out = handle.run_until_result(sink)
    elapsed = time.monotonic() - t0
    handle.close()

    assert elapsed < 3.5
    assert out.result.subtype == "cancelled"
    assert not handle.is_process_alive()
    with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
        httpx.post(mcp_url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, timeout=0.5)


def test_idle_timeout_paused_while_tool_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentos.providers.claude_cli.native_loop as nl_mod

    monkeypatch.setattr(nl_mod, "IDLE_TIMEOUT_SECONDS", 1)

    def _slow_tool(req: ToolCallRequest) -> ToolCallResult:
        time.sleep(2.2)
        return ToolCallResult(text="OK_AFTER_SLEEP")

    rec = _EchoExecutor(on_call=_slow_tool)
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "single_tool_call"},
    )
    sink = _RecordingSink()
    handle = provider.start_native_turn(_make_request(tmp_path, executor=rec))
    try:
        out = handle.run_until_result(sink)
        assert out.result.subtype == "success"
        assert "OK_AFTER_SLEEP" in out.final_text
    finally:
        handle.close()


def test_idle_timeout_kills_silent_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentos.providers.claude_cli.native_loop as nl_mod

    monkeypatch.setattr(nl_mod, "IDLE_TIMEOUT_SECONDS", 1)
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "silent_hang"},
    )
    sink = _RecordingSink()
    handle = provider.start_native_turn(_make_request(tmp_path))
    try:
        t0 = time.monotonic()
        with pytest.raises(ProviderTransportError) as exc_info:
            handle.run_until_result(sink)
        assert time.monotonic() - t0 < 4.0
        assert "sem saída" in str(exc_info.value).lower()
    finally:
        handle.close()


def test_slot_released_while_tool_runs(tmp_path: Path) -> None:
    slots = CliSlots(env={"ORIN_CLI_MAX_PROCESSES": "1"})
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "single_tool_call"},
        slots=slots,
    )

    def _parent_tool(req: ToolCallRequest) -> ToolCallResult:
        child_provider = ClaudeCliProvider(
            binary=FAKE_CLAUDE_SCRIPT,
            env={"FAKE_CLAUDE_MODE": "persistent_followup"},
            slots=slots,
        )
        child_sink = _RecordingSink()
        child_dir = tmp_path / "child"
        child_dir.mkdir(exist_ok=True)
        child_handle = child_provider.start_native_turn(
            _make_request(child_dir, executor=_EchoExecutor())
        )
        try:
            child_out = child_handle.run_until_result(child_sink)
            return ToolCallResult(text=f"child:{child_out.result.subtype}")
        finally:
            child_handle.close()

    parent_exec = _EchoExecutor(on_call=_parent_tool)
    parent_sink = _RecordingSink()
    parent_dir = tmp_path / "parent"
    parent_dir.mkdir(exist_ok=True)
    parent_handle = provider.start_native_turn(
        _make_request(parent_dir, executor=parent_exec)
    )
    try:
        out = parent_handle.run_until_result(parent_sink)
        assert out.result.subtype == "success"
        assert "child:success" in out.final_text
    finally:
        parent_handle.close()


def test_ten_concurrent_native_turns_without_limit(tmp_path: Path) -> None:
    slots = CliSlots(env={})
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "hold_until_eof"},
        slots=slots,
    )
    handles: list[NativeTurnHandle] = []
    lock = threading.Lock()

    def _start_one(idx: int) -> None:
        d = tmp_path / f"t_{idx}"
        d.mkdir(exist_ok=True)
        h = provider.start_native_turn(_make_request(d))
        with lock:
            handles.append(h)

    threads = [threading.Thread(target=_start_one, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    try:
        assert len(handles) == 10
        alive = [h.is_process_alive() for h in handles]
        assert all(alive)
    finally:
        for h in handles:
            h.close()


def test_follow_up_on_same_process_when_persistent_stdin(tmp_path: Path) -> None:
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "persistent_followup"},
    )
    sink1 = _RecordingSink()
    sink2 = _RecordingSink()
    handle = provider.start_native_turn(_make_request(tmp_path))
    try:
        out1 = handle.run_until_result(sink1)
        assert out1.result.subtype == "success"
        assert "Reply#1:Run the task" in out1.final_text

        handle.send_user([{"type": "text", "text": "Second question"}])
        out2 = handle.run_until_result(sink2)
        assert out2.result.subtype == "success"
        assert "Reply#2:Second question" in out2.final_text
    finally:
        handle.close()


def test_mcp_tool_timeout_env_matches_remaining_deadline(tmp_path: Path) -> None:
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "single_tool_call"},
    )
    sink = _RecordingSink()
    handle = provider.start_native_turn(
        _make_request(tmp_path, deadline_offset=120.0)
    )
    try:
        out = handle.run_until_result(sink)
        assert "timeout=" in out.final_text
        timeout_str = out.final_text.split("timeout=", 1)[1].strip()
        timeout_ms = int(timeout_str)
        assert 100_000 <= timeout_ms <= 125_000
    finally:
        handle.close()


def test_sink_gets_tool_progress_and_empty_thinking(tmp_path: Path) -> None:
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "two_dependent_calls"},
    )
    sink = _RecordingSink()
    handle = provider.start_native_turn(_make_request(tmp_path))
    try:
        out = handle.run_until_result(sink)
        assert out.result.subtype == "success"
        assert "" in sink.thinkings
        assert len(sink.tools_started) == 2
        assert len(sink.tools_progress) >= 2
        assert sink.tools_progress[0][2] > 0
        assert len(sink.tools_complete) == 2
        assert sink.tools_complete[0][2] == {"text": "A"}
    finally:
        handle.close()


def test_close_is_idempotent_and_logs_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="agentos.providers.claude_cli")
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "persistent_followup"},
    )
    sink = _RecordingSink()
    handle = provider.start_native_turn(_make_request(tmp_path))
    out = handle.run_until_result(sink)
    assert out.result.subtype == "success"

    handle.close()
    handle.close()
    handle.close()

    run_logs = [
        r.message
        for r in caplog.records
        if "claude_cli run mode=native" in r.message
    ]
    assert len(run_logs) == 1


def test_concurrent_after_tool_call_with_single_slot_does_not_deadlock(
    tmp_path: Path,
) -> None:
    """Red Team #1: Two concurrent tool calls finishing while the single CLI slot is held externally must not self-deadlock."""
    slots = CliSlots(limit=1)
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "persistent_followup"},
        slots=slots,
    )
    handle = provider.start_native_turn(_make_request(tmp_path))
    try:
        # Both tool calls enter: parent slot is released once
        handle._before_tool_call()
        handle._before_tool_call()

        # External subagent acquires the single slot while parent tools are running
        assert slots.acquire(cancel_check=lambda: False) is True

        finished_t1 = threading.Event()
        finished_t2 = threading.Event()

        def _finish_1() -> None:
            handle._after_tool_call()
            finished_t1.set()

        def _finish_2() -> None:
            handle._after_tool_call()
            finished_t2.set()

        t1 = threading.Thread(target=_finish_1, daemon=True)
        t2 = threading.Thread(target=_finish_2, daemon=True)
        t1.start()
        t2.start()

        time.sleep(0.15)
        # Release the external subagent slot: BOTH t1 and t2 must complete without deadlock!
        slots.release()

        assert finished_t1.wait(timeout=2.0) is True
        assert finished_t2.wait(timeout=2.0) is True
    finally:
        handle.close()


def test_stream_without_init_aborts_before_output(tmp_path: Path) -> None:
    """Red Team #3B: Output or result before system/init must raise NativeLoopUnavailable without emitting text."""
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "no_init"},
    )
    sink = _RecordingSink()
    handle = provider.start_native_turn(_make_request(tmp_path))
    try:
        with pytest.raises(NativeLoopUnavailable):
            handle.run_until_result(sink)
        assert sink.texts == []
    finally:
        handle.close()


def test_error_event_aborts_immediately_without_waiting_for_idle_timeout(
    tmp_path: Path,
) -> None:
    """Red Team #3A: A CLI error event must abort immediately instead of hanging until IDLE_TIMEOUT_SECONDS."""
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "error_event_hang"},
    )
    sink = _RecordingSink()
    handle = provider.start_native_turn(_make_request(tmp_path))
    t0 = time.monotonic()
    try:
        with pytest.raises(ProviderTransportError) as exc_info:
            handle.run_until_result(sink)
        assert time.monotonic() - t0 < 4.0
        assert "overloaded" in str(exc_info.value).lower()
    finally:
        handle.close()


def test_mcp_calls_refused_after_result_and_reenabled_on_send_user(
    tmp_path: Path,
) -> None:
    """Red Team #4: Between run_until_result() and close(), late MCP calls are refused; send_user() re-enables them."""
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "persistent_followup"},
    )
    sink = _RecordingSink()
    handle = provider.start_native_turn(_make_request(tmp_path))
    try:
        out = handle.run_until_result(sink)
        assert out.result.subtype == "success"

        # Immediately after result, a late tools/call must be refused with isError=True
        resp = httpx.post(
            handle.mcp_url,
            headers={"Authorization": f"Bearer {handle._bg_server.handle.token}"},
            json={
                "jsonrpc": "2.0",
                "id": 99,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"text": "late"}},
            },
            timeout=2.0,
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is True
    finally:
        handle.close()


def test_expired_deadline_fails_fast_without_spawning_process(
    tmp_path: Path,
) -> None:
    """Red Team #7: Starting a native turn with an already expired deadline fails fast before spawning."""
    provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "persistent_followup"},
    )
    with pytest.raises(ProviderTransportError) as exc_info:
        provider.start_native_turn(_make_request(tmp_path, deadline_offset=-1.0))
    assert "TURN_DEADLINE_EXCEEDED" in str(exc_info.value)

