from __future__ import annotations

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.lifecycle import HookEvent as E, LifecycleBus, LifecycleDecision
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticTurnRuntime


def _turn():
    return {"turn_id": "t", "conversation_id": "c", "user_id": "u", "workspace_id": "w", "agent_id": "a", "execution_id": "e",
            "provider": "openrouter", "model_id": "m", "user_message_id": "um", "assistant_message_id": "am"}


class Store:
    def __init__(self):
        self.turn = _turn()
        self.deltas = []
        self.finished = []
    def load(self, turn_id): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "go"}]
    def lifecycle(self, turn, state, **payload): pass
    def delta(self, turn, text): self.deltas.append(text)
    def finish(self, turn, *, failed=False, code=None): self.finished.append((failed, code))


def _text(value):
    return normalize_sse([f'data: {{"choices":[{{"delta":{{"content":"{value}"}},"finish_reason":"stop"}}]}}', "data: [DONE]"], provider="openrouter")


class Texts:
    def __init__(self, *values):
        self.values = list(values)
        self.requests = []
    def stream(self, request):
        self.requests.append(request)
        return _text(self.values.pop(0) if self.values else "end")


class Toolset:
    stop_requested = None
    def schemas(self, allowed=None): return []
    def is_read_only(self, name): return True
    def argument_names(self, name): return frozenset({"path"})
    def invoke(self, name, arguments, call_id=None):
        self.stop_requested = "hook says stop"
        return ToolOutcome("succeeded", "ok", "x")


def test_stop_hook_block_continues_the_turn_with_feedback() -> None:
    bus = LifecycleBus()
    calls = {"n": 0}
    payloads = []

    def decide(event):
        if event.name is E.STOP:
            payloads.append(dict(event.payload))
            calls["n"] += 1
            if calls["n"] == 1:
                return LifecycleDecision(blocked=True, reason="run the tests first")
        return None

    bus.subscribe(decide)
    provider = Texts("first", "second")
    result = AgenticTurnRuntime(store=Store(), provider=provider, lifecycle=bus).run("t")
    assert result.state == "completed" and len(provider.requests) == 2
    assert any("[Hook Stop] run the tests first" in str(m.get("content")) for m in provider.requests[1]["messages"])
    assert payloads[0]["stop_hook_active"] is False and payloads[1]["stop_hook_active"] is True


def test_stop_continuations_are_capped() -> None:
    bus = LifecycleBus()
    bus.subscribe(lambda e: LifecycleDecision(blocked=True, reason="again") if e.name is E.STOP else None)
    provider = Texts(*["x"] * 20)
    result = AgenticTurnRuntime(store=Store(), provider=provider, lifecycle=bus).run("t")
    assert result.state == "completed" and len(provider.requests) == 6


def test_prompt_block_reason_short_circuits() -> None:
    store = Store()
    provider = Texts("never")
    result = AgenticTurnRuntime(store=store, provider=provider, prompt_block_reason="secrets in prompt").run("t")
    assert result.error_code == "HOOK_BLOCKED_PROMPT" and provider.requests == []
    assert "secrets in prompt" in "".join(store.deltas) and store.finished == [(False, "HOOK_BLOCKED_PROMPT")]


def test_pre_compact_block_and_instructions() -> None:
    bus = LifecycleBus()
    mode = {"block": True}
    bus.subscribe(lambda e: (LifecycleDecision(blocked=True) if mode["block"] else LifecycleDecision(additional_context=["keep API names"])) if e.name is E.PRE_COMPACT else None)
    runtime = AgenticTurnRuntime(store=Store(), provider=Texts(), lifecycle=bus)
    messages = [{"role": "user", "content": "task"}]
    for i in range(12):
        messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 50})
    sources = []
    runtime._request_compaction_summary = lambda source, turn, code_mode=False: sources.append(source) or "## Arquivos tocados\n- a\n## Decisões\n- b\n## Dados apurados\n- c\n## Pendências\n- d"
    before = len(messages)
    runtime._run_compaction(messages, _turn())
    assert len(messages) == before and sources == []
    runtime._run_compaction(messages, _turn(), allow_block=False)
    assert sources and len(messages) < before
    mode["block"] = False
    fresh = [{"role": "user", "content": "task"}]
    for i in range(12):
        fresh.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"d{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
        fresh.append({"role": "tool", "tool_call_id": f"d{i}", "content": "y" * 50})
    runtime._run_compaction(fresh, _turn())
    assert "keep API names" in sources[-1]


def test_tool_stop_request_ends_the_turn() -> None:
    class ToolThenText:
        def __init__(self):
            self.n = 0
        def stream(self, request):
            self.n += 1
            if self.n == 1:
                return normalize_sse(['data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read_file","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}', "data: [DONE]"], provider="openrouter")
            return _text("should not run")

    store = Store()
    provider = ToolThenText()
    result = AgenticTurnRuntime(store=store, provider=provider, toolset=Toolset()).run("t")
    assert result.error_code == "HOOK_STOPPED" and provider.n == 1 and store.finished[-1] == (False, "HOOK_STOPPED")
