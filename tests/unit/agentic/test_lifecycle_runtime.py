from __future__ import annotations

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticTurnRuntime


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
        "workspace_id": "workspace-1", "agent_id": "agent-1", "execution_id": "execution-1",
        "provider": "openrouter", "model_id": "m", "user_message_id": "u1", "assistant_message_id": "a1",
    }


class Store:
    def __init__(self) -> None:
        self.turn = _turn()

    def load(self, turn_id): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "go"}]
    def lifecycle(self, turn, state, **payload): pass
    def delta(self, turn, text): pass
    def finish(self, turn, *, failed=False, code=None): pass


class ToolThenText:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, request):
        self.calls += 1
        if self.calls == 1:
            return normalize_sse([
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read_file","arguments":"{\\"path\\":\\"a\\"}"}}]},"finish_reason":"tool_calls"}]}',
                "data: [DONE]",
            ], provider="openrouter")
        return normalize_sse(['data: {"choices":[{"delta":{"content":"final answer"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")


class Toolset:
    def schemas(self, allowed=None): return []
    def is_read_only(self, name): return True
    def argument_names(self, name): return frozenset({"path"})
    def invoke(self, name, arguments):
        return ToolOutcome("succeeded", "ok", "content")


class Broken:
    def stream(self, request):
        raise RuntimeError("down")


def _bus():
    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    return bus, events


def test_batch_and_stop_are_emitted() -> None:
    bus, events = _bus()
    result = AgenticTurnRuntime(store=Store(), provider=ToolThenText(), toolset=Toolset(), lifecycle=bus).run("turn-1")
    assert result.state == "completed"
    names = [event.name for event in events]
    assert names == [HookEvent.POST_TOOL_BATCH, HookEvent.STOP]
    batch = dict(events[0].payload)
    assert batch["tool_calls"] == [{"tool_name": "Read", "tool_use_id": "c1", "status": "succeeded"}]
    stop = dict(events[1].payload)
    assert stop["stop_hook_active"] is False and stop["last_assistant_message"] == "final answer"


def test_failure_emits_stop_failure() -> None:
    bus, events = _bus()
    result = AgenticTurnRuntime(store=Store(), provider=Broken(), toolset=Toolset(), lifecycle=bus).run("turn-1")
    assert result.state == "failed"
    assert [event.name for event in events] == [HookEvent.STOP_FAILURE]
    assert dict(events[0].payload)["error_code"] == result.error_code


def test_compaction_emits_pre_and_post() -> None:
    bus, events = _bus()
    runtime = AgenticTurnRuntime(store=Store(), provider=ToolThenText(), toolset=Toolset(), lifecycle=bus)
    messages: list[dict[str, object]] = [{"role": "user", "content": "task"}]
    for index in range(12):
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{index}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": "x" * 200})
    runtime._request_compaction_summary = lambda source, turn, code_mode=False: (
        "## Arquivos tocados\n- a\n## Decisões\n- b\n## Dados apurados\n- c\n## Pendências\n- d"
    )
    runtime._run_compaction(messages, _turn())
    names = [event.name for event in events]
    assert names == [HookEvent.PRE_COMPACT, HookEvent.POST_COMPACT, HookEvent.SESSION_START]
    assert dict(events[2].payload)["source"] == "compact"
    assert dict(events[0].payload)["trigger"] == "auto"


def test_no_bus_is_fine() -> None:
    assert AgenticTurnRuntime(store=Store(), provider=ToolThenText(), toolset=Toolset()).run("turn-1").state == "completed"


class TextOnly:
    def stream(self, request):
        return normalize_sse(['data: {"choices":[{"delta":{"content":"final answer"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")


class CaveatToolset(Toolset):
    def code_completion_gate(self):
        return False, "run the tests first"


class WaitingToolset(Toolset):
    def invoke(self, name, arguments):
        return ToolOutcome("succeeded", "waiting", "wait for the user", {"wait_for_user": True})


class CutMidStream:
    def stream(self, request):
        yield from normalize_sse(['data: {"choices":[{"delta":{"content":"partial"}}]}'], provider="openrouter")
        raise RuntimeError("connection cut")


class BrokenHistoryStore(Store):
    def history_for_turn(self, turn):
        raise RuntimeError("store unavailable")


def _terminal_events(events) -> list[HookEvent]:
    return [event.name for event in events if event.name in {HookEvent.STOP, HookEvent.STOP_FAILURE}]


def test_completed_with_caveats_emits_exactly_one_stop() -> None:
    from agentos.agentic.runtime import AgenticLimits

    bus, events = _bus()
    runtime = AgenticTurnRuntime(
        store=Store(), provider=TextOnly(), toolset=CaveatToolset(), lifecycle=bus,
        limits=AgenticLimits(max_iterations=1),
    )
    result = runtime.run("turn-1")
    assert result.state == "completed_with_caveats"
    assert _terminal_events(events) == [HookEvent.STOP]
    assert dict(events[-1].payload)["stop_reason"] == "completed_with_caveats"


def test_waiting_user_emits_exactly_one_stop() -> None:
    bus, events = _bus()
    result = AgenticTurnRuntime(store=Store(), provider=ToolThenText(), toolset=WaitingToolset(), lifecycle=bus).run("turn-1")
    assert result.state == "waiting_user"
    assert _terminal_events(events) == [HookEvent.STOP]
    assert dict(events[-1].payload)["stop_reason"] == "waiting_user"


def test_effect_reconciliation_emits_exactly_one_stop_failure() -> None:
    bus, events = _bus()
    runtime = AgenticTurnRuntime(
        store=Store(), provider=ToolThenText(), toolset=Toolset(), lifecycle=bus,
        reconciliation_required=lambda _turn: True,
    )
    result = runtime.run("turn-1")
    assert result.state == "reconciliation_required"
    assert _terminal_events(events) == [HookEvent.STOP_FAILURE]
    assert dict(events[-1].payload)["error_code"] == "EFFECT_RECONCILIATION_REQUIRED"


def test_interrupted_stream_with_output_emits_exactly_one_stop_failure() -> None:
    bus, events = _bus()
    result = AgenticTurnRuntime(store=Store(), provider=CutMidStream(), toolset=Toolset(), lifecycle=bus).run("turn-1")
    assert result.state == "reconciliation_required"
    assert _terminal_events(events) == [HookEvent.STOP_FAILURE]
    assert dict(events[-1].payload)["error_code"] == "PROVIDER_STREAM_INTERRUPTED"


def test_exception_escaping_run_emits_stop_failure_and_reraises() -> None:
    import pytest

    bus, events = _bus()
    runtime = AgenticTurnRuntime(store=BrokenHistoryStore(), provider=TextOnly(), toolset=Toolset(), lifecycle=bus)
    with pytest.raises(RuntimeError, match="store unavailable"):
        runtime.run("turn-1")
    assert _terminal_events(events) == [HookEvent.STOP_FAILURE]


def test_each_tool_call_carries_its_own_call_id() -> None:
    seen: list[object] = []

    class RecordingToolset(Toolset):
        def invoke(self, name, arguments, *, call_id=None):
            seen.append(call_id)
            return ToolOutcome("succeeded", "ok", "content")

    AgenticTurnRuntime(store=Store(), provider=ToolThenText(), toolset=RecordingToolset(), lifecycle=_bus()[0]).run("turn-1")
    assert seen == ["c1"]
