from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
        "workspace_id": "workspace-1", "agent_id": "agent-1", "execution_id": "execution-1",
        "provider": "openrouter", "model_id": "local-model",
        "user_message_id": "user-message-1", "assistant_message_id": "assistant-message-1",
    }


class Store:
    def __init__(self) -> None:
        self.turn = _turn()
        self.events: list[tuple[str, dict[str, object]]] = []
        self.finished: list[tuple[bool, str | None]] = []

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, object]]:
        return [{"role": "user", "content": "read a.txt"}]

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self.events.append((state, payload))

    def delta(self, turn: dict[str, object], text: str) -> None:
        pass

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        self.finished.append((failed, code))


class Provider:
    def __init__(self, tool_name: str, arguments: dict[str, object]) -> None:
        self.requests: list[dict[str, object]] = []
        self.tool_name = tool_name
        self.arguments = arguments

    def stream(self, request: dict[str, object]):
        self.requests.append(request)
        if len(self.requests) == 1:
            args = json.dumps(json.dumps(self.arguments))[1:-1]
            line = (
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"'
                + self.tool_name + '","arguments":"' + args + '"}}]},"finish_reason":"tool_calls"}]}'
            )
            return normalize_sse([line, "data: [DONE]"], provider="openrouter")
        return normalize_sse(['data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")


def _workspace(tmp_path: Path) -> ConversationWorkspace:
    workspace = ConversationWorkspace(tmp_path, "conv-public-names")
    (workspace.root / "a.txt").write_text("hello\n", encoding="utf-8")
    return workspace


def test_toolset_resolves_and_invokes_public_names(tmp_path: Path) -> None:
    toolset = AgentToolset(_workspace(tmp_path), public_tool_names=True)
    assert toolset.resolve("Read").name == "read_file"
    assert toolset.is_read_only("Read") is True
    outcome = toolset.invoke("Read", {"file_path": "a.txt"})
    assert outcome.status == "succeeded"
    assert "hello" in outcome.content


def test_flag_off_keeps_internal_names_for_the_model(tmp_path: Path) -> None:
    toolset = AgentToolset(_workspace(tmp_path))
    assert toolset.name_translator is None
    provider = Provider("read_file", {"path": "a.txt"})
    result = AgenticTurnRuntime(store=Store(), provider=provider, toolset=toolset).run("turn-1")
    assert result.state == "completed"
    names = {item["function"]["name"] for item in provider.requests[0]["tools"]}
    assert "read_file" in names and "Read" not in names


def test_runtime_speaks_public_names_to_the_model_and_internal_names_to_orin(tmp_path: Path) -> None:
    store = Store()
    toolset = AgentToolset(_workspace(tmp_path), public_tool_names=True)
    provider = Provider("Read", {"file_path": "a.txt"})
    result = AgenticTurnRuntime(store=store, provider=provider, toolset=toolset).run("turn-1")

    assert result.state == "completed"
    tools = {item["function"]["name"]: item["function"] for item in provider.requests[0]["tools"]}
    assert "Read" in tools and "read_file" not in tools
    assert "file_path" in tools["Read"]["parameters"]["properties"]
    # the second request replays the call under its public name and shape
    assistant = next(m for m in provider.requests[1]["messages"] if m.get("role") == "assistant" and m.get("tool_calls"))
    call = assistant["tool_calls"][0]["function"]
    assert call["name"] == "Read"
    assert json.loads(call["arguments"]) == {"file_path": "a.txt"}
    tool_message = next(m for m in provider.requests[1]["messages"] if m.get("role") == "tool")
    assert "hello" in str(tool_message["content"])
    # Orin's own activity keeps the internal id the UI understands
    finished = [payload for state, payload in store.events if state == "tool_finished"]
    assert finished and finished[0]["tool_name"] == "read_file" and finished[0]["status"] == "succeeded"


def test_hostile_timeout_from_model_completes_the_turn(tmp_path: Path) -> None:
    store = Store()
    toolset = AgentToolset(_workspace(tmp_path), public_tool_names=True)
    provider = Provider("Bash", {"command": "echo hi", "timeout": "--5"})
    result = AgenticTurnRuntime(store=store, provider=provider, toolset=toolset).run("turn-1")
    assert result.state == "completed"
    assert any(state == "tool_finished" for state, _ in store.events)


def test_flag_off_hostile_timeout_with_lifecycle_does_not_crash_turn(tmp_path: Path) -> None:
    from agentos.agentic.lifecycle import LifecycleBus

    store = Store()
    toolset = AgentToolset(_workspace(tmp_path), lifecycle=LifecycleBus())
    provider = Provider("run_command", {"command": "echo hi", "timeout_seconds": "--5"})
    result = AgenticTurnRuntime(store=store, provider=provider, toolset=toolset).run("turn-1")
    assert result.state == "completed"
