from __future__ import annotations

import threading
import time
from typing import Any, Callable, Sequence
from unittest.mock import MagicMock

import pytest

from agentos.providers.claude_cli.native_loop import (
    NativeTurnHandle,
    _SlotReleasingExecutor,
)
from agentos.providers.cli_queue import CliSlotQueue, CliSlotTicket
from agentos.providers.cli_tools_mcp import McpToolSpec, ToolCallRequest, ToolCallResult


class _PolicyExecutor:
    def __init__(
        self,
        *,
        on_call: Callable[[ToolCallRequest], ToolCallResult] | None = None,
        releases: Callable[[ToolCallRequest], bool] | None = None,
    ) -> None:
        self._on_call = on_call
        if releases is not None:
            self.releases_cli_slot = releases  # type: ignore[method-assign]
        self.calls: list[ToolCallRequest] = []

    def list_tools(self) -> Sequence[McpToolSpec]:
        return [
            McpToolSpec(
                name="echo",
                description="echo",
                input_schema={"type": "object", "properties": {}},
                read_only=True,
            )
        ]

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult:
        self.calls.append(request)
        if self._on_call is not None:
            return self._on_call(request)
        return ToolCallResult(text="ok")


def _make_handle(
    slots: CliSlotQueue,
    *,
    ticket: CliSlotTicket | None = None,
    conversation_id: str = "conv-main",
) -> NativeTurnHandle:
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_bg = MagicMock()
    fake_bg.handle.in_flight = 0
    return NativeTurnHandle(
        proc=fake_proc,
        cmd=["claude"],
        requested_model="claude-opus-5-5",
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
        ticket=ticket,
        conversation_id=conversation_id,
    )


def test_non_releasing_tool_keeps_parent_ticket_and_counts_in_flight() -> None:
    slots = CliSlotQueue(limit=1, min_free_memory_mb=0)
    parent_ticket = slots.enqueue("conv-main")
    assert parent_ticket.wait(0.0)
    assert slots.active_count() == 1

    observed: dict[str, Any] = {}

    def _on_call(_req: ToolCallRequest) -> ToolCallResult:
        observed["active_during"] = slots.active_count()
        observed["holds_during"] = handle._holds_slot
        observed["ticket_during"] = handle._ticket
        observed["in_flight_during"] = handle.in_flight
        return ToolCallResult(text="done")

    inner = _PolicyExecutor(on_call=_on_call, releases=lambda _r: False)
    wrapped = _SlotReleasingExecutor(inner)
    handle = _make_handle(slots, ticket=parent_ticket)
    wrapped.handle = handle

    try:
        res = wrapped.call_tool(ToolCallRequest(name="Read", arguments={"file_path": "a.py"}))
        assert res.text == "done"
        assert observed["active_during"] == 1
        assert observed["holds_during"] is True
        assert observed["ticket_during"] is parent_ticket
        assert observed["in_flight_during"] == 1

        # After call returns: ticket is still the exact same object, never released or reacquired
        assert slots.active_count() == 1
        assert handle._holds_slot is True
        assert handle._ticket is parent_ticket
        assert handle.in_flight == 0
    finally:
        handle.close()
    assert slots.active_count() == 0


def test_releasing_tool_releases_and_reacquires_ticket() -> None:
    slots = CliSlotQueue(limit=1, min_free_memory_mb=0)
    parent_ticket = slots.enqueue("conv-main")
    assert parent_ticket.wait(0.0)

    observed: dict[str, Any] = {}

    def _on_call(_req: ToolCallRequest) -> ToolCallResult:
        observed["active_during"] = slots.active_count()
        observed["holds_during"] = handle._holds_slot
        observed["in_flight_during"] = handle.in_flight
        return ToolCallResult(text="done")

    inner = _PolicyExecutor(on_call=_on_call, releases=lambda _r: True)
    wrapped = _SlotReleasingExecutor(inner)
    handle = _make_handle(slots, ticket=parent_ticket)
    wrapped.handle = handle

    try:
        wrapped.call_tool(ToolCallRequest(name="Agent", arguments={"prompt": "x"}))
        assert observed["active_during"] == 0
        assert observed["holds_during"] is False
        assert observed["in_flight_during"] == 1

        assert slots.active_count() == 1
        assert handle._holds_slot is True
        assert handle._ticket is not None
        assert handle._ticket is not parent_ticket
        assert handle.in_flight == 0
    finally:
        handle.close()
    assert slots.active_count() == 0


def test_executor_without_releases_cli_slot_falls_back_to_releasing() -> None:
    slots = CliSlotQueue(limit=1, min_free_memory_mb=0)
    parent_ticket = slots.enqueue("conv-main")
    assert parent_ticket.wait(0.0)

    observed_active: list[int] = []

    def _on_call(_req: ToolCallRequest) -> ToolCallResult:
        observed_active.append(slots.active_count())
        return ToolCallResult(text="ok")

    inner = _PolicyExecutor(on_call=_on_call, releases=None)
    assert not hasattr(inner, "releases_cli_slot")
    wrapped = _SlotReleasingExecutor(inner)
    handle = _make_handle(slots, ticket=parent_ticket)
    wrapped.handle = handle

    try:
        wrapped.call_tool(ToolCallRequest(name="Read", arguments={}))
        assert observed_active == [0]
        assert slots.active_count() == 1
        assert handle._holds_slot is True
    finally:
        handle.close()
    assert slots.active_count() == 0


@pytest.mark.parametrize("first_finishes", ["non_releasing", "releasing"])
def test_mixed_concurrent_calls_reacquire_slot_exactly_once(first_finishes: str) -> None:
    slots = CliSlotQueue(limit=1, min_free_memory_mb=0)
    parent_ticket = slots.enqueue("conv-main")
    assert parent_ticket.wait(0.0)

    both_entered = threading.Barrier(2, timeout=3.0)
    allow_first = threading.Event()
    allow_second = threading.Event()
    first_done = threading.Event()
    active_during: dict[str, int] = {}

    def _on_call(req: ToolCallRequest) -> ToolCallResult:
        name = req.name
        both_entered.wait(timeout=3.0)
        active_during[name] = slots.active_count()
        if (name == "Read" and first_finishes == "non_releasing") or (
            name == "Agent" and first_finishes == "releasing"
        ):
            allow_first.wait(timeout=3.0)
        else:
            allow_second.wait(timeout=3.0)
        return ToolCallResult(text=name)

    inner = _PolicyExecutor(
        on_call=_on_call,
        releases=lambda req: req.name == "Agent",
    )
    wrapped = _SlotReleasingExecutor(inner)
    handle = _make_handle(slots, ticket=parent_ticket)
    wrapped.handle = handle

    errors: list[BaseException] = []

    def _run(name: str, done_ev: threading.Event | None = None) -> None:
        try:
            wrapped.call_tool(ToolCallRequest(name=name, arguments={}))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            if done_ev is not None:
                done_ev.set()

    try:
        t_read = threading.Thread(
            target=_run,
            args=("Read", first_done if first_finishes == "non_releasing" else None),
            daemon=True,
        )
        t_agent = threading.Thread(
            target=_run,
            args=("Agent", first_done if first_finishes == "releasing" else None),
            daemon=True,
        )
        t_read.start()
        t_agent.start()

        # Both entered: releasing call released the slot, both count toward in_flight
        allow_first.set()
        assert first_done.wait(timeout=3.0)
        # Second call is still running, so slot must NOT have been reacquired yet
        assert slots.active_count() == 0
        assert handle._holds_slot is False
        assert handle.in_flight == 1

        allow_second.set()
        t_read.join(timeout=3.0)
        t_agent.join(timeout=3.0)
        assert not t_read.is_alive()
        assert not t_agent.is_alive()
        assert not errors
        assert active_during == {"Read": 0, "Agent": 0}
        assert slots.active_count() == 1
        assert handle._holds_slot is True
        assert handle.in_flight == 0
    finally:
        handle.close()
    assert slots.active_count() == 0


def test_p11_parent_launching_two_background_subagents_with_limit_two_does_not_block() -> None:
    """Plan §6.2 P11: limit=2, parent launches 2 background subagents (taking slots) -> parent call returns without blocking."""
    slots = CliSlotQueue(limit=2, min_free_memory_mb=0)
    parent_ticket = slots.enqueue("conv-main")
    assert parent_ticket.wait(0.0)
    assert slots.active_count() == 1

    child_tickets: list[CliSlotTicket] = []
    child_lock = threading.Lock()
    child1_acquired = threading.Event()
    child2_queued = threading.Event()
    release_children = threading.Event()
    child_threads: list[threading.Thread] = []

    def _spawn_background_child(
        conv_id: str,
        *,
        on_queued: threading.Event | None = None,
        signal_acquired: threading.Event | None = None,
    ) -> None:
        t = slots.enqueue(conv_id)
        if on_queued is not None:
            on_queued.set()
        if t.wait(timeout=5.0):
            with child_lock:
                child_tickets.append(t)
            if signal_acquired is not None:
                signal_acquired.set()
            release_children.wait(timeout=5.0)
            t.release()

    spawn_idx = [0]

    def _on_call(req: ToolCallRequest) -> ToolCallResult:
        spawn_idx[0] += 1
        idx = spawn_idx[0]
        th = threading.Thread(
            target=_spawn_background_child,
            kwargs={
                "conv_id": f"conv-child-{idx}",
                "on_queued": child2_queued if idx == 2 else None,
                "signal_acquired": child1_acquired if idx == 1 else None,
            },
            daemon=True,
        )
        child_threads.append(th)
        th.start()
        if idx == 1:
            assert child1_acquired.wait(timeout=3.0)
        elif idx == 2:
            assert child2_queued.wait(timeout=3.0)
        return ToolCallResult(text=f"spawned-{idx}")

    inner = _PolicyExecutor(
        on_call=_on_call,
        releases=lambda req: not bool(req.arguments.get("run_in_background")),
    )
    wrapped = _SlotReleasingExecutor(inner)
    handle = _make_handle(slots, ticket=parent_ticket)
    wrapped.handle = handle

    try:
        # Both background spawns return immediately without waiting for slots,
        # even though child 1 occupies slot 2 (limit=2 is now full with parent + child 1)
        # and child 2 is queued waiting for a slot!
        r1 = wrapped.call_tool(
            ToolCallRequest(name="Agent", arguments={"prompt": "task1", "run_in_background": True})
        )
        r2 = wrapped.call_tool(
            ToolCallRequest(name="Agent", arguments={"prompt": "task2", "run_in_background": True})
        )
        assert r1.text == "spawned-1"
        assert r2.text == "spawned-2"
        assert handle._holds_slot is True
        assert handle._ticket is parent_ticket
        assert slots.active_count() == 2

        release_children.set()
        for th in child_threads:
            th.join(timeout=3.0)
            assert not th.is_alive()

        assert slots.active_count() == 1
        assert handle._holds_slot is True
    finally:
        release_children.set()
        handle.close()
    assert slots.active_count() == 0
