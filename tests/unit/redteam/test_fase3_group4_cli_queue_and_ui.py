"""Adversarial Red Team suite for Phase 3 Group 4 (Tasks 12 & 13: commits b078006, b49d726).

Covers Surface 5:
1. Deadlock prevention between `CliSlotQueue`, `ask_user`, nested subagents, parallel read-only tool
   batches (`ToolBatchCollector`), and cancellation during `_after_tool_call` slot reacquisition.
2. Slot leak prevention on subprocess spawn exceptions (`FileNotFoundError`, `OSError`,
   `CliSecurityAbort`, `NativeLoopUnavailable`), cancellation while waiting, generator `.close()`,
   and idempotent `CliSlotTicket.release()`.
3. Per-conversation fairness (starving conversation jumping ahead of 5 queued subagents from a busy
   conversation) and 10 simultaneous subagents when `limit=None`.
4. Free-memory guard (`min_free_memory_mb`, `ORIN_CLI_MIN_FREE_MEMORY_MB=0`, `free_memory_mb()`),
   including waking the next queued process when `active_count()` drops to 0 even if free RAM stays
   below `min_free_memory_mb`.
5. Waiting > 180s (`MAX_STREAM_KEEPALIVE_STALL_SECONDS`) without stall timeout, turn deadline
   enforcement during slot wait, `provider_waiting` deduplication in `NativeTurnRunner`, and
   `wait_reason` transition (`"limit"` -> `"memory"`) at the same queue position.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentos.agentic.agent_tools import AgentToolset, ToolDefinition, ToolOutcome
from agentos.agentic.native_turn import RuntimeToolExecutor
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import (
    MAX_STREAM_KEEPALIVE_STALL_SECONDS,
    AgenticLimits,
    AgenticTurnRuntime,
)
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.claude_cli.errors import (
    CliSecurityAbort,
    NativeLoopUnavailable,
    ProviderTransportError,
)
from agentos.providers.claude_cli.native_loop import (
    NativeTurnHandle,
    NativeTurnOutcome,
    NativeTurnRequest,
    NativeTurnSink,
    _SlotReleasingExecutor,
)
from agentos.providers.claude_cli.provider import (
    DEFAULT_CLAUDE_CLI_BRIDGE_URL,
    ClaudeCliProvider,
    clear_circuit_breaker,
)
from agentos.providers.cli_queue import (
    CliSlotQueue,
    acquire_cli_slot,
    free_memory_mb,
    set_cli_slot_queue,
)
from agentos.providers.cli_tools_mcp import (
    McpToolSpec,
    ToolCallRequest,
    ToolCallResult,
)
from agentos.providers.models import FinishReason
from tests.unit.agentic.test_native_turn import (
    FakeNativeProvider,
    FakeStore,
    ScriptedHandle,
    _make_init,
    _make_result,
)
from tests.unit.providers.claude_cli.fakes import FakeClaudePopen


FAKE_CLAUDE_SCRIPT = str(
    Path(__file__).resolve().parents[1] / "providers" / "claude_cli" / "fake_claude.py"
)


@pytest.fixture(autouse=True)
def _clean_global_state():
    set_cli_slot_queue(None)
    clear_circuit_breaker()
    yield
    set_cli_slot_queue(None)
    clear_circuit_breaker()


class _RecordingSink:
    def __init__(self) -> None:
        self.inits: list[Any] = []
        self.texts: list[str] = []

    def on_init(self, init: Any) -> None:
        self.inits.append(init)

    def on_text(self, text: str) -> None:
        self.texts.append(text)

    def on_thinking(self, text: str) -> None:
        pass

    def on_tool_use_started(self, tool_use_id: str, tool_name: str, *_a: Any) -> None:
        pass

    def on_tool_input_progress(
        self, tool_use_id: str, tool_name: str, bytes_received: int
    ) -> None:
        pass

    def on_tool_use_complete(
        self, tool_use_id: str, tool_name: str, tool_input: Any, *_a: Any
    ) -> None:
        pass

    def on_assistant_usage(self, message_id: str, usage: Any) -> None:
        pass

    def on_compacted(self) -> None:
        pass

    def on_rate_limit(self, state: Any) -> None:
        pass


class _SimpleToolExecutor:
    def __init__(self, on_call=None) -> None:
        self._on_call = on_call

    def list_tools(self) -> list[McpToolSpec]:
        return [
            McpToolSpec(
                name="echo",
                description="Echo tool",
                input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
            )
        ]

    def is_known_tool(self, name: str) -> bool:
        return name == "echo"

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult:
        if self._on_call is not None:
            return self._on_call(request)
        return ToolCallResult(text=f"echo:{request.arguments.get('msg', '')}")


def _valid_ndjson_lines(reply_text: str = "ok") -> list[str]:
    import json

    return [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "s-1",
                "model": "claude-sonnet-4-6",
                "apiKeySource": "none",
                "mcp_servers": [],
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": reply_text,
                "stop_reason": "end_turn",
                "num_turns": 1,
                "duration_ms": 5,
                "session_id": "s-1",
            }
        ),
    ]


# ============================================================================
# SURFACE 5.1: Deadlock Prevention (limit=1, nested subagent, ask_user, parallel batch, cancel)
# ============================================================================


def test_s5_1_nested_parent_and_child_subagent_with_limit_1_no_deadlock(
    tmp_path: Path,
) -> None:
    """With `ORIN_CLI_MAX_PROCESSES=1` (`limit=1`), a native parent turn calling a tool
    that runs both a child `.stream()` turn and a child `.start_native_turn()` turn must
    release the single slot during `call_tool`, let both children acquire and release it,
    and then reacquire the slot for the parent without deadlocking.
    """
    slots = CliSlotQueue(limit=1, min_free_memory_mb=0)
    parent_provider = ClaudeCliProvider(
        binary=FAKE_CLAUDE_SCRIPT,
        env={"FAKE_CLAUDE_MODE": "single_tool_call"},
        slots=slots,
    )

    observed_during_tool: list[int] = []

    def _run_nested_children(_req: ToolCallRequest) -> ToolCallResult:
        # Slot must have been released by parent before tool body runs
        observed_during_tool.append(slots.active_count())

        # Child 1: text-protocol stream()
        stream_child = ClaudeCliProvider(
            base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            model="claude-sonnet-4-6",
            binary="C:/fake/claude.exe",
            slots=slots,
            popen_factory=lambda cmd, **kw: FakeClaudePopen(
                cmd, ndjson_lines=_valid_ndjson_lines("stream-child-ok"), env=kw.get("env")
            ),
        )
        try:
            items = list(
                stream_child.stream(
                    {
                        "conversation_id": "conv-parent",
                        "messages": [{"role": "user", "content": "subagent 1"}],
                    }
                )
            )
        finally:
            stream_child.close()
        assert any(i.kind == StreamKind.TEXT and "stream-child-ok" in (i.text or "") for i in items)
        observed_during_tool.append(slots.active_count())

        # Child 2: native loop start_native_turn()
        native_child = ClaudeCliProvider(
            binary=FAKE_CLAUDE_SCRIPT,
            env={"FAKE_CLAUDE_MODE": "persistent_followup"},
            slots=slots,
        )
        child_cwd = tmp_path / "native_child"
        child_cwd.mkdir(exist_ok=True)
        child_handle = native_child.start_native_turn(
            NativeTurnRequest(
                model="claude-sonnet-4-6",
                system_prompt="child",
                user_content=[{"type": "text", "text": "hello child"}],
                executor=_SimpleToolExecutor(),
                cancel_check=lambda: False,
                deadline_monotonic=time.monotonic() + 30.0,
                max_turns=2,
                cwd=child_cwd,
                conversation_id="conv-parent",
            )
        )
        try:
            assert slots.active_count() == 1
            child_out = child_handle.run_until_result(_RecordingSink())
            assert child_out.result.subtype == "success"
        finally:
            child_handle.close()
            native_child.close()

        observed_during_tool.append(slots.active_count())
        return ToolCallResult(text="both-children-succeeded")

    parent_cwd = tmp_path / "parent"
    parent_cwd.mkdir(exist_ok=True)
    handle = parent_provider.start_native_turn(
        NativeTurnRequest(
            model="claude-sonnet-4-6",
            system_prompt="parent",
            user_content=[{"type": "text", "text": "delegate"}],
            executor=_SimpleToolExecutor(on_call=_run_nested_children),
            cancel_check=lambda: False,
            deadline_monotonic=time.monotonic() + 30.0,
            max_turns=3,
            cwd=parent_cwd,
            conversation_id="conv-parent",
        )
    )
    try:
        out = handle.run_until_result(_RecordingSink())
        assert out.result.subtype == "success"
        assert "both-children-succeeded" in out.final_text
        assert observed_during_tool == [0, 0, 0]
    finally:
        handle.close()
        parent_provider.close()

    assert slots.active_count() == 0


def test_s5_1_parallel_readonly_tool_batch_releases_once_and_reacquires_once(
    tmp_path: Path,
) -> None:
    """When 3 threads simultaneously invoke `call_tool` on `_SlotReleasingExecutor` (as in a
    read-only batch gathered by `ToolBatchCollector`), the slot is released exactly once on
    entry, stays at 0 while any of the 3 calls is still active, and is reacquired exactly
    once when the last call finishes.
    """
    slots = CliSlotQueue(limit=1, min_free_memory_mb=0)
    initial_ticket = slots.enqueue("conv-batch")
    assert initial_ticket.wait(0.0)
    assert slots.active_count() == 1

    all_entered = threading.Barrier(3, timeout=5.0)
    release_first_two = threading.Event()
    release_third = threading.Event()
    active_snapshots: list[int] = []
    lock = threading.Lock()

    def _inner_call(req: ToolCallRequest) -> ToolCallResult:
        idx = int(req.arguments["idx"])
        all_entered.wait()
        with lock:
            active_snapshots.append(slots.active_count())
        if idx < 2:
            release_first_two.wait(timeout=5.0)
        else:
            release_third.wait(timeout=5.0)
        return ToolCallResult(text=f"done-{idx}")

    inner = _SimpleToolExecutor(on_call=_inner_call)
    wrapped = _SlotReleasingExecutor(inner)

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_bg = MagicMock()
    fake_bg.handle.in_flight = 0

    handle = NativeTurnHandle(
        proc=fake_proc,
        cmd=["claude"],
        requested_model="claude-sonnet-4-6",
        server_name="orin",
        bg_server=fake_bg,
        temp_files=[],
        slots=slots,
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        clock=time.monotonic,
        kill_tree_fn=lambda _p: None,
        log_run_fn=lambda **_kw: None,
        stdout_queue=MagicMock(),
        stderr_chunks=[],
        stdout_thread=MagicMock(),
        stderr_thread=MagicMock(),
        t0=time.monotonic(),
        ticket=initial_ticket,
        conversation_id="conv-batch",
    )
    wrapped.handle = handle

    results: list[str] = []
    errors: list[BaseException] = []

    def _worker(i: int) -> None:
        try:
            res = wrapped.call_tool(ToolCallRequest(name="echo", arguments={"idx": i}))
            with lock:
                results.append(res.text)
        except BaseException as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()

    # Wait until all 3 threads are inside _inner_call
    while True:
        with lock:
            if len(active_snapshots) == 3:
                break
        time.sleep(0.01)

    assert active_snapshots == [0, 0, 0]
    assert slots.active_count() == 0

    # Let the first two threads finish; the 3rd is still inside _inner_call
    release_first_two.set()
    threads[0].join(timeout=5.0)
    threads[1].join(timeout=5.0)
    assert not threads[0].is_alive()
    assert not threads[1].is_alive()
    # Slot must STILL be released because thread 2 is still running
    assert slots.active_count() == 0

    # Now let the 3rd thread finish -> must reacquire exactly 1 slot
    release_third.set()
    threads[2].join(timeout=5.0)
    assert not threads[2].is_alive()
    assert not errors
    assert sorted(results) == ["done-0", "done-1", "done-2"]
    assert slots.active_count() == 1

    handle.close()
    assert slots.active_count() == 0


def test_s5_1_cancel_while_reacquiring_slot_in_after_tool_call_does_not_hang_or_leak() -> None:
    """If `cancel_check` becomes True while `_after_tool_call` is blocked waiting to
    reacquire a busy slot, `call_tool` must unblock promptly and leak zero slots.
    """
    slots = CliSlotQueue(limit=1, min_free_memory_mb=0)
    parent_ticket = slots.enqueue("conv-parent")
    assert parent_ticket.wait(0.0)

    cancelled = {"flag": False}
    other_holder: dict[str, Any] = {}
    tool_entered = threading.Event()
    allow_tool_exit = threading.Event()

    def _inner_call(_req: ToolCallRequest) -> ToolCallResult:
        # While parent's slot is released, another conversation takes the only slot
        t_other = slots.enqueue("conv-other")
        assert t_other.wait(0.0)
        other_holder["ticket"] = t_other
        tool_entered.set()
        allow_tool_exit.wait(timeout=5.0)
        return ToolCallResult(text="ok")

    wrapped = _SlotReleasingExecutor(_SimpleToolExecutor(on_call=_inner_call))
    fake_proc = MagicMock()
    fake_proc.poll.return_value = 0
    fake_bg = MagicMock()
    fake_bg.handle.in_flight = 0

    waiting_seen = threading.Event()
    handle = NativeTurnHandle(
        proc=fake_proc,
        cmd=["claude"],
        requested_model="claude-sonnet-4-6",
        server_name="orin",
        bg_server=fake_bg,
        temp_files=[],
        slots=slots,
        cancel_check=lambda: cancelled["flag"],
        deadline_monotonic=time.monotonic() + 30.0,
        clock=time.monotonic,
        kill_tree_fn=lambda _p: None,
        log_run_fn=lambda **_kw: None,
        stdout_queue=MagicMock(),
        stderr_chunks=[],
        stdout_thread=MagicMock(),
        stderr_thread=MagicMock(),
        t0=time.monotonic(),
        ticket=parent_ticket,
        conversation_id="conv-parent",
        on_waiting_slot=lambda *_a: waiting_seen.set(),
    )
    wrapped.handle = handle

    call_done = threading.Event()

    def _run_call() -> None:
        wrapped.call_tool(ToolCallRequest(name="echo", arguments={}))
        call_done.set()

    t = threading.Thread(target=_run_call, daemon=True)
    t.start()
    assert tool_entered.wait(timeout=2.0)
    allow_tool_exit.set()

    # Wait until _after_tool_call is actively waiting in the queue
    assert waiting_seen.wait(timeout=2.0)
    assert slots.active_count() == 1

    # Flip cancel_check -> _after_tool_call must unblock without waiting for conv-other
    t0 = time.monotonic()
    cancelled["flag"] = True
    assert call_done.wait(timeout=1.0), "_after_tool_call hung after cancel_check became True"
    assert time.monotonic() - t0 < 0.5

    handle.close()
    other_holder["ticket"].release()
    assert slots.active_count() == 0


# ============================================================================
# SURFACE 5.2: Slot Leak Prevention on Exception, Cancellation, and Idempotent Release
# ============================================================================


@pytest.mark.parametrize(
    "exc_to_raise",
    [
        FileNotFoundError("claude binary missing"),
        OSError("WinError 1455 paging file too small"),
        CliSecurityAbort("unsafe working directory"),
    ],
)
def test_s5_2_stream_releases_slot_on_spawn_exceptions(exc_to_raise: Exception) -> None:
    """If `subprocess.Popen` (or setup after ticket acquisition) raises in `ClaudeCliProvider.stream`,
    the acquired ticket is always released and the next queued conversation gets the slot.
    """
    slots = CliSlotQueue(limit=1, min_free_memory_mb=0)

    def _failing_popen(*_a: Any, **_kw: Any) -> Any:
        # At the moment Popen is called, the ticket is held (active_count == 1)
        assert slots.active_count() == 1
        raise exc_to_raise

    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-sonnet-4-6",
        binary="C:/fake/claude.exe",
        slots=slots,
        popen_factory=_failing_popen,
    )
    try:
        with pytest.raises(type(exc_to_raise)):
            list(
                provider.stream(
                    {
                        "conversation_id": "conv-fail",
                        "messages": [{"role": "user", "content": "hi"}],
                    }
                )
            )
    finally:
        provider.close()

    assert slots.active_count() == 0
    next_ticket = slots.enqueue("conv-next")
    assert next_ticket.wait(0.0) is True
    next_ticket.release()
    assert slots.active_count() == 0


@pytest.mark.parametrize(
    ("exc_to_raise", "expected_exc"),
    [
        (FileNotFoundError("claude binary missing"), NativeLoopUnavailable),
        (OSError("spawn error"), NativeLoopUnavailable),
        (CliSecurityAbort("security abort"), CliSecurityAbort),
        (NativeLoopUnavailable("mcp server failed", status_code=503), NativeLoopUnavailable),
    ],
)
def test_s5_2_start_native_turn_releases_slot_on_spawn_exceptions(
    tmp_path: Path,
    exc_to_raise: Exception,
    expected_exc: type[Exception],
) -> None:
    """If `start_native_turn_for_provider` raises after acquiring a slot ticket, the ticket
    is always released (`active_count() == 0`) and the next queued conversation can proceed.
    """
    slots = CliSlotQueue(limit=1, min_free_memory_mb=0)

    def _failing_popen(*_a: Any, **_kw: Any) -> Any:
        assert slots.active_count() == 1
        raise exc_to_raise

    provider = ClaudeCliProvider(
        binary="C:/fake/claude.exe",
        slots=slots,
        popen_factory=_failing_popen,
    )
    req = NativeTurnRequest(
        model="claude-sonnet-4-6",
        system_prompt="sys",
        user_content=[{"type": "text", "text": "hi"}],
        executor=_SimpleToolExecutor(),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=2,
        cwd=tmp_path,
        conversation_id="conv-native-fail",
    )
    try:
        with pytest.raises(expected_exc):
            provider.start_native_turn(req)
    finally:
        provider.close()

    assert slots.active_count() == 0
    next_ticket = slots.enqueue("conv-after-fail")
    assert next_ticket.wait(0.0) is True
    next_ticket.release()
    assert slots.active_count() == 0


def test_s5_2_cancel_during_wait_in_stream_and_native_turn_advances_next_in_queue(
    tmp_path: Path,
) -> None:
    """Cancelling a waiting `.stream()` or `.start_native_turn()` removes its ticket from
    the queue, advances the next waiting conversation from position 2 to position 1, and
    leaks zero slots.
    """
    slots = CliSlotQueue(limit=1, min_free_memory_mb=0)
    held = slots.enqueue("conv-A")
    assert held.wait(0.0)

    # 1. Start conv-B waiting in provider.stream(), then cancel it while conv-C is also queued
    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-sonnet-4-6",
        binary="C:/fake/claude.exe",
        slots=slots,
    )
    cancel_b = {"now": False}
    gen_b = provider.stream(
        {"conversation_id": "conv-B", "messages": [{"role": "user", "content": "hi"}]},
        cancel_check=lambda: cancel_b["now"],
    )
    first_item = next(gen_b)
    assert first_item.kind == StreamKind.WAITING
    assert first_item.queue_position == 1

    ticket_c = slots.enqueue("conv-C")
    assert ticket_c.position() == 2

    # Cancel conv-B and exhaust generator
    cancel_b["now"] = True
    assert list(gen_b) == []
    assert ticket_c.position() == 1

    # 2. Also test start_native_turn cancelling while queued behind ticket_c
    cancel_d = {"now": False}
    d_waiting = threading.Event()
    d_errors: list[BaseException] = []

    def _run_native_d() -> None:
        try:
            provider.start_native_turn(
                NativeTurnRequest(
                    model="claude-sonnet-4-6",
                    system_prompt="sys",
                    user_content=[{"type": "text", "text": "hi"}],
                    executor=_SimpleToolExecutor(),
                    cancel_check=lambda: cancel_d["now"],
                    deadline_monotonic=time.monotonic() + 30.0,
                    max_turns=1,
                    cwd=tmp_path,
                    conversation_id="conv-D",
                    on_waiting_slot=lambda pos, _lim: d_waiting.set() if pos == 2 else None,
                )
            )
        except BaseException as exc:
            d_errors.append(exc)

    t_d = threading.Thread(target=_run_native_d, daemon=True)
    t_d.start()
    assert d_waiting.wait(timeout=2.0)
    cancel_d["now"] = True
    t_d.join(timeout=2.0)
    assert len(d_errors) == 1
    assert isinstance(d_errors[0], NativeLoopUnavailable)
    assert getattr(d_errors[0], "status_code", None) == 499

    # Release conv-A -> conv-C must immediately get the slot
    held.release()
    assert ticket_c.wait(0.2) is True
    assert slots.active_count() == 1
    ticket_c.release()
    provider.close()
    assert slots.active_count() == 0


def test_s5_2_ticket_release_is_strictly_idempotent_when_waiting_and_when_granted() -> None:
    """Calling `release()` multiple times on a waiting or granted `CliSlotTicket` is strictly
    idempotent: `active_count()` never goes negative and never releases another holder's slot.
    """
    slots = CliSlotQueue(limit=2, min_free_memory_mb=0)
    t1 = slots.enqueue("conv-1")
    t2 = slots.enqueue("conv-2")
    t3_waiting = slots.enqueue("conv-3")
    assert t1.wait(0.0) and t2.wait(0.0)
    assert not t3_waiting.wait(0.0)
    assert slots.active_count() == 2

    # Release waiting ticket 5 times -> active_count must stay 2
    for _ in range(5):
        t3_waiting.release()
    assert slots.active_count() == 2

    # Release t1 5 times -> active_count must drop to 1 (t2 is still active!)
    for _ in range(5):
        t1.release()
    assert slots.active_count() == 1

    # Release t2 5 times -> active_count must drop to 0, never negative
    for _ in range(5):
        t2.release()
    assert slots.active_count() == 0


# ============================================================================
# SURFACE 5.3: Fair Per-Conversation Queue & 10 Simultaneous Subagents
# ============================================================================


def test_s5_3_fairness_with_limit_2_starving_conversation_b_passes_five_queued_from_a() -> None:
    """When `limit=2` and conversation A holds both active slots plus 5 queued subagents,
    conversation B arriving later with 0 active slots is at position 1 and gets the slot
    as soon as one of A's active processes finishes.
    """
    slots = CliSlotQueue(limit=2, min_free_memory_mb=0)
    a1, a2 = slots.enqueue("conv-A"), slots.enqueue("conv-A")
    assert a1.wait(0.0) and a2.wait(0.0)

    a_queued = [slots.enqueue("conv-A") for _ in range(5)]
    b_ticket = slots.enqueue("conv-B")
    assert b_ticket.position() == 1
    for idx, t in enumerate(a_queued, start=2):
        assert t.position() == idx

    a1.release()
    assert b_ticket.wait(0.1) is True
    assert all(not t.wait(0.0) for t in a_queued)

    b_ticket.release()
    a2.release()
    for t in a_queued:
        assert t.wait(0.1) is True
        t.release()
    assert slots.active_count() == 0


def test_s5_3_fairness_with_limit_1_position_1_must_not_be_stolen_by_releasing_conversation() -> None:
    """When `limit=1` and conversation A has 1 active slot and 5 queued subagents, and
    conversation B arrives with 0 active slots, `b_ticket.position()` reports `1` (ahead
    of A's 5 queued subagents at positions 2..6). When A's active process finishes, B
    (which was reported as 1st in line) MUST receive the freed slot rather than A's 5
    queued subagents starving B until all 6 of A's processes complete.
    """
    slots = CliSlotQueue(limit=1, min_free_memory_mb=0)
    a_active = slots.enqueue("conv-A")
    assert a_active.wait(0.0)

    a_queued = [slots.enqueue("conv-A") for _ in range(5)]
    for idx, t in enumerate(a_queued, start=1):
        assert t.position() == idx

    # Conversation B arrives after all 5 of A's subagents
    b_ticket = slots.enqueue("conv-B")
    assert b_ticket.position() == 1
    for idx, t in enumerate(a_queued, start=2):
        assert t.position() == idx

    # As soon as A's active process finishes, B (which was at position 1) must get the slot
    a_active.release()
    assert b_ticket.wait(0.1) is True, (
        "Conversation B was at position() == 1, but when conv-A released its slot, "
        "conv-A's queued subagent stole the slot and starved conv-B!"
    )
    assert all(not t.wait(0.0) for t in a_queued)

    b_ticket.release()
    for t in a_queued:
        assert t.wait(0.1) is True
        t.release()
    assert slots.active_count() == 0


def test_s5_3_ten_simultaneous_subagents_same_and_different_conversations_unlimited() -> None:
    """10 subagents from the same conversation (and 10 from different conversations) run
    simultaneously when `limit=None` and sufficient memory is available (`active_count() == 10`).
    """
    slots_same = CliSlotQueue(limit=None, min_free_memory_mb=1024, memory_probe=lambda: 8192)
    tickets_same = [slots_same.enqueue("conv-same") for _ in range(10)]
    assert all(t.wait(0.0) for t in tickets_same)
    assert slots_same.active_count() == 10
    for t in tickets_same:
        t.release()
    assert slots_same.active_count() == 0

    slots_diff = CliSlotQueue(limit=None, min_free_memory_mb=1024, memory_probe=lambda: 8192)
    tickets_diff = [slots_diff.enqueue(f"conv-{i}") for i in range(10)]
    assert all(t.wait(0.0) for t in tickets_diff)
    assert slots_diff.active_count() == 10
    for t in tickets_diff:
        t.release()
    assert slots_diff.active_count() == 0


# ============================================================================
# SURFACE 5.4: Memory Protection & `free_memory_mb()`
# ============================================================================


def test_s5_4_low_memory_never_blocks_first_and_wakes_second_when_first_releases_at_200mb() -> None:
    """With `min_free_memory_mb=1024` and `memory_probe` permanently returning `200` MB:
    1. First process (`active_count() == 0`) is granted immediately.
    2. Second and third processes are held with `wait_reason() == "memory"`.
    3. When `first.release()` is called while RAM is STILL 200 MB (`active_count()` drops to 0),
       `second` becomes the sole active process and MUST be woken up and granted immediately
       (never stalling the entire queue when `active_count() == 0`), while `third` remains held
       by `"memory"` until `second` finishes or memory rises!
    """
    ram = {"mb": 200}
    slots = CliSlotQueue(limit=None, min_free_memory_mb=1024, memory_probe=lambda: ram["mb"])

    first = slots.enqueue("conv-1")
    assert first.wait(0.0) is True
    assert slots.active_count() == 1

    second = slots.enqueue("conv-2")
    third = slots.enqueue("conv-3")
    assert second.wait(0.05) is False
    assert second.wait_reason() == "memory"
    assert third.wait(0.0) is False
    assert third.wait_reason() == "memory"

    # Wait on `second` in a background thread to verify condition notification wakes it
    # as soon as `first.release()` drops active_count to 0 (with RAM still at 200 MB).
    second_granted = threading.Event()

    def _wait_second() -> None:
        if second.wait(2.0):
            second_granted.set()

    t = threading.Thread(target=_wait_second, daemon=True)
    t.start()

    first.release()
    assert second_granted.wait(timeout=0.5), (
        "Second process was not woken and granted when first released at 200 MB!"
    )
    assert slots.active_count() == 1
    assert third.wait(0.05) is False
    assert third.wait_reason() == "memory"

    # Now raise free memory to 1024 MB -> third is granted while second is still active
    ram["mb"] = 1024
    assert third.wait(0.2) is True
    assert slots.active_count() == 2

    second.release()
    third.release()
    assert slots.active_count() == 0


def test_s5_4_env_min_free_memory_zero_disables_guard() -> None:
    """`ORIN_CLI_MIN_FREE_MEMORY_MB=0` disables the memory guard completely."""
    q = CliSlotQueue(env={"ORIN_CLI_MIN_FREE_MEMORY_MB": "0"}, memory_probe=lambda: 0)
    assert q.min_free_memory_mb == 0
    tickets = [q.enqueue(f"c-{i}") for i in range(5)]
    assert all(t.wait(0.0) for t in tickets)
    for t in tickets:
        t.release()


def test_s5_4_free_memory_mb_live_windows_and_failure_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`free_memory_mb()` returns a positive integer on the current Windows host, and
    gracefully returns `None` without raising on unsupported platforms or OS errors.
    """
    live_mb = free_memory_mb()
    if sys.platform == "win32":
        assert isinstance(live_mb, int) and live_mb > 0

    # Simulate unknown platform (e.g. macOS/Darwin)
    import agentos.providers.cli_queue as cq

    monkeypatch.setattr(cq.os, "name", "posix")
    monkeypatch.setattr(cq.sys, "platform", "darwin")
    assert free_memory_mb() is None

    # Simulate Linux with unreadable /proc/meminfo
    monkeypatch.setattr(cq.sys, "platform", "linux")
    monkeypatch.setattr(
        cq.Path,
        "read_text",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("proc unavailable")),
    )
    assert free_memory_mb() is None


# ============================================================================
# SURFACE 5.5: Waiting > 180s, Deadline Enforcement, Native Deduplication & Reason Transition
# ============================================================================


def test_s5_5_classic_runtime_wait_300s_does_not_stall_timeout_but_enforces_deadline(
    tmp_path: Path,
) -> None:
    """Waiting 300s (> `MAX_STREAM_KEEPALIVE_STALL_SECONDS = 180`) in `StreamKind.WAITING`
    followed by `StreamKind.KEEPALIVE` does not trigger a keepalive stall timeout,
    Carry-over check: if the turn's total `deadline` expires during `StreamKind.WAITING`,
    the runtime aborts immediately with `TURN_DEADLINE_EXCEEDED`.
    """
    assert MAX_STREAM_KEEPALIVE_STALL_SECONDS == 180

    now = {"t": datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)}

    def _clock() -> datetime:
        return now["t"]

    def _advance(seconds: int) -> None:
        now["t"] = now["t"] + timedelta(seconds=seconds)

    class _Provider300sThenKeepalive:
        def stream(self, _request: dict[str, object]):
            for seq in range(1, 7):
                _advance(50)  # 6 * 50s = 300s > 180s
                yield NormalizedStreamItem(
                    StreamKind.WAITING,
                    sequence=seq,
                    queue_position=1,
                    queue_limit=2,
                )
            # A keepalive 10s after the 300s wait must NOT think 310s elapsed without progress
            _advance(10)
            yield NormalizedStreamItem(StreamKind.KEEPALIVE, sequence=7)
            _advance(1)
            yield NormalizedStreamItem(StreamKind.TEXT, sequence=8, text="concluido")
            yield NormalizedStreamItem(
                StreamKind.FINISH, sequence=9, finish_reason=FinishReason.STOP
            )

    store_ok = FakeStore()
    rt_ok = AgenticTurnRuntime(
        store=store_ok,
        provider=_Provider300sThenKeepalive(),
        toolset=AgentToolset(ConversationWorkspace(tmp_path / "w_ok", "w_ok")),
        limits=AgenticLimits(
            max_iterations=3,
            max_actions=3,
            deadline=timedelta(minutes=15),
        ),
        clock=_clock,
    )
    res_ok = rt_ok.run("t1", turn=store_ok.turn)
    assert res_ok.state == "completed"

    # Now configure a 120s deadline: waiting 150s in WAITING must abort with TURN_DEADLINE_EXCEEDED
    store_dl = FakeStore()
    rt_dl = AgenticTurnRuntime(
        store=store_dl,
        provider=_Provider300sThenKeepalive(),
        toolset=AgentToolset(ConversationWorkspace(tmp_path / "w_dl", "w_dl")),
        limits=AgenticLimits(
            max_iterations=3,
            max_actions=3,
            deadline=timedelta(seconds=120),
        ),
        clock=_clock,
    )
    res_dl = rt_dl.run("t1", turn=store_dl.turn)
    assert res_dl.state == "failed"
    assert res_dl.error_code == "TURN_DEADLINE_EXCEEDED"


def test_s5_5_native_turn_runner_deduplicates_repeated_on_waiting_slot_for_same_position(
    tmp_path: Path,
) -> None:
    """In `NativeTurnRunner`, if `req.on_waiting_slot(1, 2)` is invoked multiple times with
    the same queue position `(1, 2)` (e.g. on each poll tick or across tool-call slot
    reacquisitions at the same position), `NativeTurnRunner` must deduplicate by position
    just like `AgenticTurnRuntime.run` rather than flooding the activity store with
    duplicate `provider_waiting` events for the same position.
    """
    store = FakeStore()
    store.turn["conversation_mode"] = "code"
    store.turn["code_mode"] = "code"

    def _handle_factory(req: NativeTurnRequest) -> ScriptedHandle:
        # Simulate polling callbacks reporting the same position (2, 2) three times,
        # then advancing to (1, 2) three times before the slot is granted.
        assert req.on_waiting_slot is not None
        for _ in range(3):
            req.on_waiting_slot(2, 2, "limit")
        for _ in range(3):
            req.on_waiting_slot(1, 2, "limit")

        def _round(
            r: NativeTurnRequest,
            sink: NativeTurnSink,
            _h: ScriptedHandle,
        ) -> NativeTurnOutcome:
            init = _make_init(r.model)
            sink.on_init(init)
            sink.on_text("pronto")
            return NativeTurnOutcome(
                result=_make_result(text="pronto"),
                init=init,
                final_text="pronto",
                exit_code=0,
            )

        return ScriptedHandle(req, [_round])

    provider = FakeNativeProvider(handle_factory=_handle_factory)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=AgentToolset(ConversationWorkspace(tmp_path / "ws_dedup", "c_dedup")),
        limits=AgenticLimits(max_iterations=4, max_actions=4, context_window_tokens=200_000),
    )
    out = runtime.run("t1", turn=store.turn)
    assert out.state == "completed"

    waiting_positions = [
        payload["position"]
        for state, payload in store.states
        if state == "provider_waiting"
    ]
    assert waiting_positions == [2, 1], (
        f"NativeTurnRunner emitted duplicate provider_waiting events for unchanged positions: {waiting_positions}"
    )


def test_s5_5_start_native_turn_respects_deadline_monotonic_while_waiting_for_cli_slot(
    tmp_path: Path,
) -> None:
    """If `request.deadline_monotonic` expires while `start_native_turn_for_provider` is
    waiting for a busy CLI slot in `acquire_cli_slot`, it must stop waiting, release its
    queued ticket, never spawn the subprocess, and fail with `TURN_DEADLINE_EXCEEDED`
    (never hanging indefinitely nor reporting HTTP 499 user cancellation).
    """
    slots = CliSlotQueue(limit=1, min_free_memory_mb=0)
    held = slots.enqueue("conv-holder")
    assert held.wait(0.0)

    spawned = {"count": 0}

    def _popen_spy(*_a: Any, **_kw: Any) -> Any:
        spawned["count"] += 1
        raise AssertionError("Popen must not be called after deadline_monotonic expired")

    provider = ClaudeCliProvider(
        binary="C:/fake/claude.exe",
        slots=slots,
        popen_factory=_popen_spy,
    )
    req = NativeTurnRequest(
        model="claude-sonnet-4-6",
        system_prompt="sys",
        user_content=[{"type": "text", "text": "hello"}],
        executor=_SimpleToolExecutor(),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 0.15,
        max_turns=2,
        cwd=tmp_path,
        conversation_id="conv-deadline",
    )

    err_holder: list[BaseException] = []
    done = threading.Event()

    def _target() -> None:
        try:
            provider.start_native_turn(req)
        except BaseException as exc:
            err_holder.append(exc)
        finally:
            done.set()

    t = threading.Thread(target=_target, daemon=True)
    t.start()

    finished_in_time = done.wait(timeout=1.0)
    held.release()
    t.join(timeout=1.0)
    provider.close()

    assert finished_in_time, (
        "start_native_turn hung past request.deadline_monotonic while waiting for a CLI slot!"
    )
    assert spawned["count"] == 0, "Subprocess was spawned after deadline_monotonic expired!"
    assert slots.active_count() == 0
    assert len(err_holder) == 1
    assert "TURN_DEADLINE_EXCEEDED" in str(err_holder[0]), (
        f"Expected TURN_DEADLINE_EXCEEDED when deadline expired during slot wait, got {err_holder[0]!r}"
    )


def test_s5_5_wait_reason_transition_from_limit_to_memory_at_same_position_emits_event(
    tmp_path: Path,
) -> None:
    """When a queued turn stays at position 1 but its block reason changes from `'limit'`
    to `'memory'` (e.g. `limit=2`, 2 processes active, 1 finishes while free RAM is 200 MB),
    both `acquire_cli_slot` and `AgenticTurnRuntime.run` must emit a new waiting notification
    reflecting the `'memory'` reason.
    """
    ram = {"mb": 2048}
    slots = CliSlotQueue(limit=2, min_free_memory_mb=1024, memory_probe=lambda: ram["mb"])
    a = slots.enqueue("conv-A")
    b = slots.enqueue("conv-B")
    assert a.wait(0.0) and b.wait(0.0)
    ram["mb"] = 200

    seen_reasons: list[tuple[int, int | None, str | None]] = []

    def _release_b_then_raise_ram() -> None:
        time.sleep(0.06)
        # Releasing B drops active_count from 2 to 1 (< limit=2), so C stays at pos=1
        # but is now blocked by 'memory' (200 MB < 1024 MB) instead of 'limit'!
        b.release()
        time.sleep(0.08)
        ram["mb"] = 2048

    t = threading.Thread(target=_release_b_then_raise_ram, daemon=True)
    t.start()

    c = acquire_cli_slot(
        "conv-C",
        on_wait=lambda pos, lim, reason: seen_reasons.append((pos, lim, reason)),
        poll_seconds=0.02,
        queue=slots,
    )
    assert c is not None
    c.release()
    a.release()

    assert seen_reasons == [(1, 2, "limit"), (1, 2, "memory")], (
        f"acquire_cli_slot failed to report wait_reason transition at position 1: {seen_reasons}"
    )
