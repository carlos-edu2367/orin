"""Integration tests for per-agent memory, isolation, recall priority, and verified promotion (WS-D5 / Criteria 6 & 7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select

from agentos.agentic.agent_definitions import VERIFIER, load_agent_definitions, parse_agent_file
from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.session import TurnSession, memories_for_task
from agentos.agentic.tools._shared import AgentToolError
from agentos.agentic.workspace import ConversationWorkspace
from agentos.memory.agent_scope import AgentMemoryScope, AgentScopedMemoryView
from agentos.memory.models_write import MemoryWriteContext
from agentos.memory.promotion import MemoryPromotionGate
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import agent_memories, memory_events, metadata
from agentos.persistence.sqlite import create_local_engine
from agentos.settings.loader import EffectiveSettings
from tests.unit.agentic.test_parallel_and_background_subagents import (
    _RecordingStore,
    _parallel_tool_calls_batch,
    _text_batch,
)


def _settings(*, agent_enabled: bool, max_prompt_chars: int = 4000, learn_from_verified_only: bool = True) -> EffectiveSettings:
    return EffectiveSettings(
        data={
            "memory": {
                "agent": {
                    "enabled": agent_enabled,
                    "maxPromptChars": max_prompt_chars,
                    "learnFromVerifiedOnly": learn_from_verified_only,
                }
            }
        },
        provenance={
            "memory.agent.enabled": "user",
            "memory.agent.maxPromptChars": "user",
            "memory.agent.learnFromVerifiedOnly": "user",
        },
        layers=(),
        errors=(),
        trusted=True,
    )


def _write_custom_agent(root: Path, name: str, *, memory_line: str = "") -> Path:
    agents_dir = root / ".orin" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    header = f"---\nname: {name}\ndescription: Especialista {name}\n"
    if memory_line:
        header += f"memory: {memory_line}\n"
    header += "---\nVoce revisa queries SQL com foco em performance.\n"
    path = agents_dir / f"{name}.md"
    path.write_text(header, encoding="utf-8")
    return path


class _RememberingChildProvider:
    """Child model that calls Remember once, optionally reads a second turn, and answers."""

    def __init__(self, fact: str, *, scope: str | None = None) -> None:
        self.fact = fact
        self.scope = scope
        self.requests: list[Any] = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            args: dict[str, Any] = {"fact": self.fact, "kind": "operational"}
            if self.scope is not None:
                args["scope"] = self.scope
            return iter(_parallel_tool_calls_batch([("call-rem-1", "remember", args)]))
        return iter(_text_batch("Revisao concluida e aprendizado registrado."))


def test_agent_definitions_validate_memory_field_and_verifier_defaults(tmp_path: Path) -> None:
    assert VERIFIER.memory == "project"

    # Missing memory field in custom agent defaults to "project"
    path_default = _write_custom_agent(tmp_path, "revisor-sql")
    defn = parse_agent_file(path_default, origin="project")
    assert defn.memory == "project"

    # Explicit valid values accepted
    for mode in ("project", "user", "local", "none"):
        p = _write_custom_agent(tmp_path, f"agent-{mode}", memory_line=mode)
        assert parse_agent_file(p, origin="project").memory == mode

    # Invalid memory value rejected
    bad_path = _write_custom_agent(tmp_path, "bad-mem", memory_line="invalid_scope")
    with pytest.raises(ValueError, match="memory"):
        parse_agent_file(bad_path, origin="project")

    catalog = load_agent_definitions(workspace_root=tmp_path, trusted=True)
    assert catalog.get("verifier") is not None
    assert catalog.get("verifier").memory == "project"
    assert catalog.get("revisor-sql") is not None
    assert catalog.get("revisor-sql").memory == "project"
    assert any("bad-mem" in err[0] or "memory" in err[1] for err in catalog.errors)


def test_remember_scope_agent_rejected_outside_enabled_subagent(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    store = PostgresAgentMemoryStore(engine, "user-1", project_id="proj-1", data_dir=tmp_path)
    workspace = ConversationWorkspace(tmp_path, "conv-main")
    toolset = AgentToolset(workspace, memory=store)

    with pytest.raises(AgentToolError, match="agent"):
        toolset.remember("Fato do agente.", scope="agent")

    with pytest.raises(AgentToolError, match="agent"):
        toolset.recall(query="Fato", scope="agent")


def test_recall_in_subagent_searches_agent_then_project_then_user_with_priority(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    store = PostgresAgentMemoryStore(engine, "user-1", project_id="proj-1", data_dir=tmp_path)
    scope = AgentMemoryScope(
        user_id="user-1",
        project_id="proj-1",
        workspace_hash="ws1",
        agent_name="revisor-sql",
        mode="project",
    )

    store.save("Padrao migracao banco postgres em user.", scope_type="user", source="user_explicit")
    store.save("Padrao migracao banco postgres em project.", scope_type="project", source="user_explicit")
    saved_agent = store.save(
        "Padrao migracao banco postgres em agent revisor.",
        scope_type="agent",
        agent_scope=scope,
        context=MemoryWriteContext(
            source_kind="agent",
            actor="agent:revisor-sql",
            agent_key="revisor-sql",
            conversation_id="conv-1",
            turn_id="turn-1",
            trust=1,
        ),
    )
    MemoryPromotionGate(engine, user_id="user-1").on_verified("turn-1", True)

    view = AgentScopedMemoryView(store, scope)
    workspace = ConversationWorkspace(tmp_path, "conv-sub")
    sub_toolset = AgentToolset(workspace, memory=view)

    # Recall searches agent + project + user with agent > project > user bonus
    matches = view.search("migracao banco postgres", limit=5)
    assert [m["scope_type"] for m in matches[:3]] == ["agent", "project", "user"]
    assert matches[0]["memory_id"] == saved_agent["memory_id"]

    # Tool-level Recall also works and returns all three in priority order
    recalled = sub_toolset.recall(query="migracao banco postgres")
    assert recalled["payload"]["count"] == 3
    lines = recalled["content"].splitlines()
    assert "agent revisor" in lines[0]
    assert "em project" in lines[1]
    assert "em user" in lines[2]

    # Parent user/project scopes are strictly read-only from subagent view
    with pytest.raises(AgentToolError):
        sub_toolset.remember("Tentativa de alterar memoria do pai.", scope="project")
    with pytest.raises(AgentToolError):
        sub_toolset.remember("Tentativa de alterar memoria global.", scope="user")


def test_criteria_6_and_7_custom_agent_proposes_isolated_memory_freezes_prompt_and_promotes_on_verify(
    tmp_path: Path,
) -> None:
    """Criterion 6 & 7:
    - With memory.agent.enabled=True, custom agent `revisor-sql` without `memory:` writes to
      scope_type='agent', agent_key='revisor-sql', current project, status='proposed'.
    - Another project does not see it; parent does not receive it in prompt.
    - Unverified proposal stays proposed; approved verification promotes to active + 'promoted' event.
    - Next run injects frozen '## O que o agente revisor-sql aprendeu' section into subagent prompt.
    """
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'crit67.db').as_posix()}")
    metadata.create_all(engine)
    try:
        turn_base = {
            "turn_id": "turn-crit-67",
            "conversation_id": "conv-crit-67",
            "execution_id": "exec-crit-67",
            "user_id": "user-1",
            "project_id": "proj-alpha",
            "project_workspace_id": "ws-proj-alpha",
            "provider": "openrouter",
            "model_id": "test-model",
            "user_message_id": "um-1",
            "assistant_message_id": "am-1",
        }
        mem_alpha = PostgresAgentMemoryStore(
            engine, "user-1", conversation_id="conv-crit-67", project_id="proj-alpha", data_dir=tmp_path / "idx_a"
        )
        child_1 = _RememberingChildProvider("Usar indice parcial WHERE deleted_at IS NULL em orders.")
        session = TurnSession(
            turn=dict(turn_base),
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id="conv-crit-67", user_id="user-1"),
            memory_store=mem_alpha,
            provider_factory=lambda: child_1,
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: child_1,
        )
        _write_custom_agent(session.workspace.root, "revisor-sql")
        session.effective_settings = _settings(agent_enabled=True, learn_from_verified_only=True)
        # Ensure agent catalog loads from trusted workspace
        session._agent_definitions = load_agent_definitions(workspace_root=session.workspace.root, trusted=True)

        outcome = session._run_subagent(subagent_type="revisor-sql", task="Revise a query da tabela orders.")
        assert outcome.status == "succeeded", outcome.content

        # Check DB row: scope_type='agent', agent_key='revisor-sql', project_id='proj-alpha', status='proposed'
        with engine.connect() as conn:
            rows = conn.execute(select(agent_memories).where(agent_memories.c.scope_type == "agent")).mappings().all()
        assert len(rows) == 1
        row = rows[0]
        mem_id = str(row["memory_id"])
        assert row["agent_key"] == "revisor-sql"
        assert row["project_id"] == "proj-alpha"
        assert row["scope_id"] == "project:proj-alpha:revisor-sql"
        assert row["status"] == "proposed"

        # Before verification: proposed memory is NOT in parent prompt or active agent memories
        assert memories_for_task(mem_alpha, "orders indice parcial") == []
        scope_alpha = AgentMemoryScope("user-1", "proj-alpha", None, "revisor-sql", "project")
        assert mem_alpha.active_for_agent_scope(scope_alpha) == []

        # Verification fails via runtime._absorb_verification -> remains proposed
        from agentos.agentic.runtime import AgenticTurnRuntime
        from agentos.agentic.tools._shared import ToolOutcome

        parent_runtime = AgenticTurnRuntime(
            store=_RecordingStore(),
            provider=child_1,
            on_verification=session._promote_agent_memories,
        )
        parent_runtime._absorb_verification(
            "report_verification",
            ToolOutcome("succeeded", "Verificação reprovada", "fail", {"passed": False, "findings": "Teste falhou"}),
        )
        assert mem_alpha.active_for_agent_scope(scope_alpha) == []

        # Verification passes via runtime._absorb_verification -> promotes to active + 'promoted'
        parent_runtime._absorb_verification(
            "report_verification",
            ToolOutcome("succeeded", "Verificação aprovada", "ok", {"passed": True, "findings": "Tudo verde"}),
        )

        with engine.connect() as conn:
            promoted_row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == mem_id)).mappings().one()
            ev_names = [
                r["event"]
                for r in conn.execute(
                    select(memory_events)
                    .where(memory_events.c.memory_id == mem_id)
                    .order_by(memory_events.c.id.asc())
                ).mappings().all()
            ]
        assert promoted_row["status"] == "active"
        assert ev_names == ["proposed", "promoted"]

        # Parent STILL does NOT receive agent memory in its prompt
        assert memories_for_task(mem_alpha, "orders indice parcial") == []

        # Another project does NOT see this memory
        mem_beta = PostgresAgentMemoryStore(
            engine, "user-1", conversation_id="conv-beta", project_id="proj-beta", data_dir=tmp_path / "idx_b"
        )
        scope_beta = AgentMemoryScope("user-1", "proj-beta", None, "revisor-sql", "project")
        assert mem_beta.active_for_agent_scope(scope_beta) == []
        assert mem_beta.search("orders indice parcial", agent_scope=scope_beta) == []

        # Next run of revisor-sql in proj-alpha injects the frozen summary in its system prompt
        # and mid-run writes do not change the frozen system prompt sent on subsequent iterations.
        child_2 = _RememberingChildProvider("Evitar SELECT * em joins grandes.")
        session2 = TurnSession(
            turn={**turn_base, "turn_id": "turn-crit-67-2"},
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id="conv-crit-67", user_id="user-1"),
            memory_store=mem_alpha,
            provider_factory=lambda: child_2,
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: child_2,
        )
        session2.effective_settings = _settings(agent_enabled=True, learn_from_verified_only=True)
        session2._agent_definitions = load_agent_definitions(workspace_root=session2.workspace.root, trusted=True)

        outcome2 = session2._run_subagent(subagent_type="revisor-sql", task="Revise o join da tabela invoices.")
        assert outcome2.status == "succeeded", outcome2.content
        assert len(child_2.requests) >= 2

        sys_prompt_iter1 = next(m["content"] for m in child_2.requests[0]["messages"] if m["role"] == "system")
        sys_prompt_iter2 = next(m["content"] for m in child_2.requests[1]["messages"] if m["role"] == "system")
        assert "## O que o agente revisor-sql aprendeu" in sys_prompt_iter1
        assert "Usar indice parcial WHERE deleted_at IS NULL em orders." in sys_prompt_iter1
        assert "Evitar SELECT * em joins grandes." not in sys_prompt_iter1
        assert sys_prompt_iter1 == sys_prompt_iter2
    finally:
        engine.dispose()
