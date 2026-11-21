"""Snapshot regression test (WS-D5 / Criterion 8):
When `memory.agent.enabled = False` (default), subagent prompt and memory behaviour
must remain 100% identical to 0.12.31.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentos.agentic.agent_definitions import load_agent_definitions
from agentos.agentic.session import TurnSession
from agentos.memory.agent_scope import AgentMemoryScope
from agentos.memory.models_write import MemoryWriteContext
from agentos.memory.promotion import MemoryPromotionGate
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from agentos.settings.loader import EffectiveSettings, empty_settings
from tests.unit.agentic.test_parallel_and_background_subagents import (
    _RecordingStore,
    _text_batch,
)


class _CapturePromptProvider:
    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    def stream(self, request: dict[str, Any]):
        for msg in request.get("messages") or ():
            if msg.get("role") == "system":
                self.system_prompts.append(str(msg.get("content") or ""))
                break
        return iter(_text_batch("OK"))


def _write_custom_agent(root: Path, name: str) -> None:
    folder = root / ".orin" / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: Especialista {name}\n---\nVoce analisa consultas SQL.\n",
        encoding="utf-8",
    )


def test_criterion_8_subagent_prompt_and_memory_identical_when_agent_memory_disabled(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'snap.db').as_posix()}")
    metadata.create_all(engine)
    try:
        turn = {
            "turn_id": "turn-snap-1",
            "conversation_id": "conv-snap-1",
            "execution_id": "exec-snap-1",
            "user_id": "user-1",
            "project_id": "proj-1",
            "project_workspace_id": "ws-proj-1",
            "provider": "openrouter",
            "model_id": "test-model",
            "user_message_id": "um-1",
            "assistant_message_id": "am-1",
        }
        mem_store = PostgresAgentMemoryStore(
            engine, "user-1", conversation_id="conv-snap-1", project_id="proj-1", data_dir=tmp_path / "idx"
        )
        # Seed an active agent memory that WOULD be injected if the flag were enabled
        scope = AgentMemoryScope("user-1", "proj-1", None, "revisor-sql", "project")
        mem_store.save(
            "Regra SQL que NAO deve aparecer com flag desligada.",
            scope_type="agent",
            agent_scope=scope,
            context=MemoryWriteContext(
                source_kind="agent",
                actor="agent:revisor-sql",
                agent_key="revisor-sql",
                conversation_id="conv-snap-1",
                turn_id="turn-seed",
                trust=1,
            ),
        )
        MemoryPromotionGate(engine, user_id="user-1").on_verified("turn-seed", True)

        # Baseline: default empty settings (memory.agent.enabled defaults to False)
        prov_default = _CapturePromptProvider()
        session_default = TurnSession(
            turn=dict(turn),
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id="conv-snap-1", user_id="user-1"),
            memory_store=mem_store,
            provider_factory=lambda: prov_default,
            workspace_root=tmp_path,
            child_provider_factory=lambda _m: prov_default,
        )
        _write_custom_agent(session_default.workspace.root, "revisor-sql")
        session_default.effective_settings = empty_settings()
        session_default._agent_definitions = load_agent_definitions(workspace_root=session_default.workspace.root, trusted=True)

        # Explicit False settings (fresh conversation_id so agent name numbering matches 1:1)
        prov_disabled = _CapturePromptProvider()
        session_disabled = TurnSession(
            turn={**turn, "conversation_id": "conv-snap-2", "turn_id": "turn-snap-2"},
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id="conv-snap-2", user_id="user-1"),
            memory_store=mem_store,
            provider_factory=lambda: prov_disabled,
            workspace_root=tmp_path,
            child_provider_factory=lambda _m: prov_disabled,
        )
        session_disabled.effective_settings = EffectiveSettings(
            data={"memory": {"agent": {"enabled": False}}},
            provenance={"memory.agent.enabled": "user"},
            layers=(),
            errors=(),
            trusted=True,
        )
        session_disabled._agent_definitions = load_agent_definitions(workspace_root=session_disabled.workspace.root, trusted=True)

        # Direct baseline prompt rendered with plain record (no agent_memory_summary)
        out_default = session_default._run_subagent(subagent_type="revisor-sql", task="Analise a tabela users.")
        out_disabled = session_disabled._run_subagent(subagent_type="revisor-sql", task="Analise a tabela users.")

        assert out_default.status == "succeeded"
        assert out_disabled.status == "succeeded"
        assert len(prov_default.system_prompts) == 1
        assert len(prov_disabled.system_prompts) == 1

        # Byte-for-byte identical system prompts
        assert prov_default.system_prompts[0] == prov_disabled.system_prompts[0]
        assert "## O que o agente" not in prov_default.system_prompts[0]
        assert "Regra SQL que NAO deve aparecer com flag desligada." not in prov_default.system_prompts[0]

        # And when the child toolset is created with flag disabled, it receives parent self.memory directly (no wrapper)
        sub_view, summary = session_disabled._subagent_memory_context(
            "revisor-sql", "revisor-sql", session_disabled._agent_definitions.get("revisor-sql")
        )
        assert sub_view is None
        assert summary == ""
    finally:
        engine.dispose()
