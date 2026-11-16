from __future__ import annotations

from pathlib import Path
from typing import Any

from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.session import TurnSession
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine


def _turn(provider: str, model_id: str) -> dict[str, Any]:
    return {
        "turn_id": f"turn-def-{provider}",
        "conversation_id": f"conv-def-{provider}",
        "user_id": "user-1",
        "execution_id": f"exe-def-{provider}",
        "provider": provider,
        "model_id": model_id,
        "user_message_id": "msg-user",
        "assistant_message_id": "msg-assistant",
        "code_mode": "code",
    }


class _SimpleStore:
    def __init__(self, turn: dict[str, Any]) -> None:
        self._turn = turn

    def load(self, _turn_id: str) -> dict[str, Any]:
        return dict(self._turn)

    def history_for_turn(self, _turn: object) -> list[dict[str, str]]:
        return [{"role": "user", "content": "Implemente a feature"}]

    def delta(self, _turn: object, text: str) -> None:
        pass

    def finish(self, _turn: object, *, failed: bool = False, code: str | None = None) -> None:
        pass

    def record(self, *args: Any, **kwargs: Any) -> None:
        pass

    def main_agent_id(self, turn: dict[str, Any]) -> str:
        return f"agent:{turn['conversation_id']}:main"


def test_codex_turn_disables_tool_deferral_and_omits_load_on_demand_from_code_prompt(tmp_path: Path) -> None:
    turn = _turn("openai-codex", "gpt-6.1-sol")
    session = TurnSession(
        turn=turn,
        store=_SimpleStore(turn),
        agents_store=None,
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path,
    )
    session.build_runtime()

    assert session.toolset._allow_deferral is False
    assert session.toolset.deferred_tool_names == frozenset()
    assert "Other tools load on demand:" not in session._system_prompt


def test_anthropic_api_turn_keeps_tool_deferral_in_code_prompt(tmp_path: Path) -> None:
    turn = _turn("anthropic", "claude-opus-4-6")
    session = TurnSession(
        turn=turn,
        store=_SimpleStore(turn),
        agents_store=None,
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path,
    )
    session.build_runtime()

    assert session.toolset._allow_deferral is True
    assert len(session.toolset.deferred_tool_names) > 0
    assert "Other tools load on demand:" in session._system_prompt


def test_subagent_receives_allow_deferral_for_its_own_provider(tmp_path: Path) -> None:
    db_file = tmp_path / "defer_sub.db"
    engine = create_local_engine(f"sqlite:///{db_file.as_posix()}")
    metadata.create_all(engine)

    turn = _turn("anthropic-cli", "claude-opus-4-6")
    agents_store = ConversationAgentStore(engine, conversation_id=turn["conversation_id"], user_id=turn["user_id"])

    observed_subagent_deferral: list[tuple[bool, frozenset[str]]] = []

    class _SubagentProvider:
        def stream(self, _request: dict[str, Any]):
            return iter(
                [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text="Codex subagent done."),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )

    session = TurnSession(
        turn=turn,
        store=_SimpleStore(turn),
        agents_store=agents_store,
        memory_store=None,
        provider_factory=_SubagentProvider,
        workspace_root=tmp_path,
        child_provider_factory=lambda _m, provider=None: _SubagentProvider(),
    )
    session.build_runtime()
    # Main agent is anthropic-cli (tool_list_changed=True) -> deferral allowed
    assert session.toolset._allow_deferral is True
    assert len(session.toolset.deferred_tool_names) > 0

    orig_toolset = session._toolset

    def _spy_toolset(*args: Any, **kwargs: Any):
        ts = orig_toolset(*args, **kwargs)
        observed_subagent_deferral.append((ts._allow_deferral, ts.deferred_tool_names))
        return ts

    # Run a subagent on openai-codex while main agent is anthropic-cli
    rec = agents_store.create(
        "codex-worker",
        "Codex UI specialist",
        parent_agent_id=session.main_agent_id,
        provider="openai-codex",
        model_id="gpt-6.1-sol",
        force_new=True,
    )
    created_subagent_tools = []
    orig_subagent_prompt = session._subagent_prompt

    def _capture_prompt(record: dict[str, object], task_text: str, subagent_tools: Any) -> str:
        created_subagent_tools.append(subagent_tools)
        return orig_subagent_prompt(record, task_text, subagent_tools)

    session._subagent_prompt = _capture_prompt  # type: ignore[method-assign]
    outcome = session._execute_subagent_run_unlocked(
        record=rec,
        agent_id=str(rec["agent_id"]),
        clean="codex-worker",
        builtin_key="general",
        builtin_spec=session._subagent_spec("general"),
        child_model_id="gpt-6.1-sol",
        current_model_id="claude-opus-4-6",
        task_text="Inspect UI",
        description="Inspect UI",
        child_provider="openai-codex",
        model_chain=("gpt-6.1-sol",),
    )
    assert outcome.status == "succeeded", outcome.content
    assert len(created_subagent_tools) == 1
    assert created_subagent_tools[0]._allow_deferral is False
    assert created_subagent_tools[0].deferred_tool_names == frozenset()
    engine.dispose()
