"""W4: canonical names and aliases resolve to the stable key in every mode."""
from __future__ import annotations

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace


def _toolset(tmp_path, **options) -> AgentToolset:
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "notes.txt").write_text("first line\nsecond line\n", encoding="utf-8")
    return AgentToolset(workspace, **options)


def test_chat_mode_resolves_canonical_names_and_their_arguments(tmp_path) -> None:
    toolset = _toolset(tmp_path)
    assert toolset.name_translator is None
    outcome = toolset.invoke("Read", {"file_path": "notes.txt"})
    assert outcome.status == "succeeded" and "first line" in outcome.content
    assert toolset.invoke("read", {"file_path": "notes.txt"}).status == "succeeded"
    assert toolset.resolve("Bash").name == "run_command"
    assert toolset.key_for("Grep") == "search_files"


def test_resolve_works_before_anything_built_the_definitions(tmp_path) -> None:
    assert _toolset(tmp_path).is_read_only("read_file") is True


def test_legacy_names_and_shapes_still_work_in_code_mode(tmp_path) -> None:
    toolset = _toolset(tmp_path, public_tool_names=True)
    assert toolset.invoke("read_file", {"path": "notes.txt"}).status == "succeeded"
    assert toolset.invoke("Read", {"file_path": "notes.txt", "force_reload": True}).status == "succeeded"


def test_unknown_and_remote_names_are_left_alone(tmp_path) -> None:
    toolset = _toolset(tmp_path)
    assert toolset.key_for("mcp__srv__tool") == "mcp__srv__tool"
    assert toolset.key_for("LaunchMissiles") == "LaunchMissiles"
    outcome = toolset.invoke("LaunchMissiles", {})
    assert outcome.status == "failed" and outcome.error_code == "UNKNOWN_TOOL"


def test_every_definition_carries_its_canonical_name(tmp_path) -> None:
    for item in _toolset(tmp_path).definitions():
        assert item.public_name and item.public_name in item.aliases
        assert item.permission_category


class _Store:
    def load(self, turn_id: str):
        return {
            "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
            "provider": "openrouter", "model_id": "model",
            "user_message_id": "user-message-1", "assistant_message_id": "assistant-message-1",
        }

    def history_for_turn(self, turn): return [{"role": "user", "content": "leia"}]
    def lifecycle(self, turn, state, **payload) -> None: ...
    def delta(self, turn, text) -> None: ...
    def finish(self, turn, *, failed: bool = False, code: str | None = None) -> None: ...


class _Toolset:
    def __init__(self) -> None:
        self.invoked: list[str] = []

    def schemas(self, allowed=None, kinds=None): return []
    def is_read_only(self, name): return name == "read_file"
    def argument_names(self, name): return None
    def key_for(self, name): return {"Read": "read_file"}.get(name, name)

    def invoke(self, name, arguments):
        self.invoked.append(name)
        return ToolOutcome("succeeded", "lido", "conteudo", {})


class _Script:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, request):
        self.calls += 1
        if self.calls > 1:
            return normalize_sse([
                'data: {"choices":[{"delta":{"content":"pronto"},"finish_reason":"stop"}]}', "data: [DONE]",
            ], provider="openrouter")
        return normalize_sse([
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":'
            '{"name":"Read","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ], provider="openrouter")


def test_the_runtime_hands_the_toolset_the_stable_key() -> None:
    toolset = _Toolset()
    AgenticTurnRuntime(
        store=_Store(), provider=_Script(), toolset=toolset, system_prompt="regras",
        limits=AgenticLimits(), context_reporting=False,
    ).run("turn-1")
    assert toolset.invoked == ["read_file"]
