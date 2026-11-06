from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.claude_cli.native_loop import (
    NativeTurnOutcome,
    NativeTurnRequest,
    NativeTurnSink,
)
from tests.unit.agentic.test_native_turn import (
    FakeNativeProvider,
    FakeStore,
    ScriptedHandle,
    _make_init,
    _make_result,
    _make_toolset,
)


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-1",
        "conversation_id": "conversation-1",
        "user_id": "user-1",
        "provider": "openrouter",
        "model_id": "local-model",
        "user_message_id": "user-message-1",
        "assistant_message_id": "assistant-message-1",
    }


class _Store:
    def __init__(self) -> None:
        self.turn = _turn()
        self.events: list[tuple[str, dict[str, object]]] = []
        self.text: list[str] = []

    def load(self, turn_id: str):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "reformule o orçamento"}]

    def lifecycle(self, turn, state, **payload) -> None:
        self.events.append((state, payload))

    def delta(self, turn, text) -> None:
        self.text.append(text)

    def finish(self, turn, *, failed: bool = False, code: str | None = None) -> None:
        ...


def _json_args(payload: dict) -> str:
    return json.dumps(json.dumps(payload))[1:-1]


def _tool_call(call_id: str, name: str, arguments: str) -> str:
    return (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"%s","function":'
        '{"name":"%s","arguments":"%s"}}]},"finish_reason":"tool_calls"}]}'
        % (call_id, name, arguments)
    )


_ANSWER = 'data: {"choices":[{"delta":{"content":"pronto"},"finish_reason":"stop"}]}'


class _ScriptedProvider:
    def __init__(self, script: list[str]) -> None:
        self.script = script
        self.requests: list[dict[str, object]] = []

    def stream(self, request):
        self.requests.append(request)
        line = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        return normalize_sse([line, "data: [DONE]"], provider="openrouter")


def _code_runtime(
    tmp_path: Path, provider: _ScriptedProvider
) -> tuple[_Store, AgenticTurnRuntime]:
    store = _Store()
    store.turn["code_mode"] = "code"
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=AgentToolset(
            ConversationWorkspace(tmp_path, "c1"), code_mode_active=True
        ),
        system_prompt="prompt",
        limits=AgenticLimits(max_iterations=None, max_actions=None),
        phase_controller=None,
    )
    return store, runtime


def test_turn_end_keeps_pending_and_in_progress_todos(tmp_path: Path):
    todos = [
        {"id": "1", "content": "a", "status": "completed", "activeForm": "a"},
        {"id": "2", "content": "b", "status": "in_progress", "activeForm": "b"},
        {"id": "3", "content": "c", "status": "pending", "activeForm": "c"},
    ]
    provider = _ScriptedProvider(
        [_tool_call("t1", "todo_write", _json_args({"todos": todos})), _ANSWER]
    )
    store, runtime = _code_runtime(tmp_path, provider)
    assert runtime.run("turn-1").state == "completed"
    assert [t["status"] for t in runtime.toolset._todos] == [  # type: ignore[union-attr]
        "completed",
        "in_progress",
        "pending",
    ]
    assert not any(
        str(p.get("invocation_id", "")).startswith("auto_todo_settle_")
        for _, p in store.events
    )


def test_native_turn_end_keeps_pending_and_in_progress_todos(tmp_path: Path):
    todos = [
        {"id": "1", "content": "a", "status": "completed", "activeForm": "a"},
        {"id": "2", "content": "b", "status": "in_progress", "activeForm": "b"},
        {"id": "3", "content": "c", "status": "pending", "activeForm": "c"},
    ]
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = _make_toolset(workspace)
    toolset.invoke("todo_write", {"todos": todos})

    def _round(
        req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle
    ) -> NativeTurnOutcome:
        init = _make_init(req.model)
        sink.on_init(init)
        return NativeTurnOutcome(
            result=_make_result(text="Done."),
            init=init,
            final_text="Done.",
            exit_code=0,
        )

    prov = FakeNativeProvider(
        handle_factory=lambda req: ScriptedHandle(req, [_round])
    )
    rt = AgenticTurnRuntime(store=store, provider=prov, toolset=toolset)
    assert rt.run("t1", turn=store.turn).state == "completed"
    assert [t["status"] for t in toolset._todos] == [
        "completed",
        "in_progress",
        "pending",
    ]
    assert not any(
        str(p.get("invocation_id", "")).startswith("auto_todo_settle_")
        for _, p in store.states
    )
