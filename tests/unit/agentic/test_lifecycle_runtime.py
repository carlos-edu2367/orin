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
    assert names == [HookEvent.PRE_COMPACT, HookEvent.POST_COMPACT]
    assert dict(events[0].payload)["trigger"] == "auto"


def test_no_bus_is_fine() -> None:
    assert AgenticTurnRuntime(store=Store(), provider=ToolThenText(), toolset=Toolset()).run("turn-1").state == "completed"
