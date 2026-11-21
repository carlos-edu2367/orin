"""Integration tests for Remember provenance, quarantine, and session wiring (WS-D4 / Criterion 9)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, select

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.session import TurnSession, memories_for_task
from agentos.agentic.tools._shared import PinnedHop
from agentos.agentic.tools.public_names import PublicToolNames
from agentos.agentic.tools.web import fetch as fetch_module
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.schema import agent_memories, memory_events, metadata
from agentos.settings.loader import EffectiveSettings


@pytest.fixture(autouse=True)
def _offline_public_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_module.clear_fetch_cache()
    monkeypatch.setattr(
        fetch_module,
        "_pinned_public_hop",
        lambda url: PinnedHop(url=url, host="docs.example.com", address="93.184.216.34"),
    )


def _memory_store(tmp_path: Path) -> tuple:
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    store = PostgresAgentMemoryStore(engine, "user:1", project_id="proj:orin", data_dir=tmp_path)
    return engine, store


def _mock_http_client(html: str = "<html><body><h1>Docs</h1><p>API v2 usa header X-Token.</p></body></html>") -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=html)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _settings_with_quarantine(enabled: bool) -> EffectiveSettings:
    return EffectiveSettings(
        data={"memory": {"quarantine": {"enabled": enabled}}},
        provenance={"memory.quarantine.enabled": "user"},
        layers=(),
        errors=(),
        trusted=True,
    )


def test_remember_schema_does_not_expose_source_kind_in_legacy_or_public_surface(tmp_path: Path) -> None:
    _, store = _memory_store(tmp_path)
    workspace = ConversationWorkspace(tmp_path, "conv-schema")
    toolset = AgentToolset(workspace, memory=store)

    legacy_def = toolset.resolve("remember")
    legacy_props = set((legacy_def.parameters.get("properties") or {}).keys())
    assert "source_kind" not in legacy_props
    assert "source_ref" not in legacy_props
    assert "trust" not in legacy_props
    assert "actor" not in legacy_props

    public_schemas = PublicToolNames().schemas_out(toolset.schemas())
    remember_public = next(
        s["function"] for s in public_schemas if s["function"]["name"] == "Remember"
    )
    public_props = set((remember_public["parameters"].get("properties") or {}).keys())
    assert "source_kind" not in public_props
    assert "source_ref" not in public_props
    assert "trust" not in public_props
    assert "actor" not in public_props


def test_criterion_9_webfetch_then_remember_is_quarantined_and_hidden_from_prompt_and_recall(tmp_path: Path) -> None:
    """Criterion 9: saída de WebFetch gravada via Remember fica quarantined, avisa o modelo,
    emite evento memory.quarantined e não aparece em memories_for_task / recent / relevant / search / Recall."""
    engine, store = _memory_store(tmp_path)
    workspace = ConversationWorkspace(tmp_path, "conv-crit9")
    quarantine_events: list[dict[str, Any]] = []
    toolset = AgentToolset(workspace, memory=store, http_client=_mock_http_client(), public_tool_names=True)
    toolset._effective_settings = _settings_with_quarantine(True)
    toolset._memory_turn_state_provider = lambda: {
        "user_message": "Leia https://docs.example.com/api e resuma.",
        "turn_id": "turn-crit9",
        "conversation_id": "conv-crit9",
        "effective_settings": toolset._effective_settings,
        "workspace_root": str(workspace.root),
    }
    toolset._on_memory_quarantined = lambda payload: quarantine_events.append(dict(payload))

    fetch_outcome = toolset.invoke("WebFetch", {"url": "https://docs.example.com/api", "prompt": "resuma"})
    assert fetch_outcome.status == "succeeded"

    rem_outcome = toolset.invoke(
        "Remember",
        {"fact": "A API v2 exige o header X-Token segundo docs.example.com.", "scope": "project"},
    )
    assert rem_outcome.status == "succeeded"
    assert "salva em quarentena, aguardando aprovação" in rem_outcome.content.lower()
    mem_id = str(rem_outcome.payload["memory_id"])
    assert rem_outcome.payload.get("status") == "quarantined"
    assert rem_outcome.payload.get("source_kind") == "web"

    # Visible event memory.quarantined emitted (no silent drop)
    assert len(quarantine_events) == 1
    assert quarantine_events[0]["memory_id"] == mem_id
    assert quarantine_events[0]["source_kind"] == "web"
    assert quarantine_events[0]["status"] == "quarantined"

    # DB row + memory_events check
    with engine.connect() as conn:
        row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == mem_id)).mappings().one()
        ev = conn.execute(select(memory_events).where(memory_events.c.memory_id == mem_id)).mappings().one()
    assert row["status"] == "quarantined"
    assert row["source_kind"] == "web"
    assert row["trust"] == 0
    assert ev["event"] == "quarantined"

    # Strictly invisible in prompt & Recall
    assert memories_for_task(store, "API v2 header X-Token") == []
    assert store.recent(limit=20) == []
    assert store.relevant("API v2 header X-Token", limit=20) == []
    assert store.search("X-Token", limit=20) == []

    recall_query = toolset.invoke("Recall", {"query": "X-Token"})
    assert recall_query.status == "succeeded"
    assert recall_query.payload.get("count") == 0

    recall_by_id = toolset.invoke("Recall", {"memory_id": mem_id})
    assert recall_by_id.status == "succeeded"
    assert recall_by_id.payload.get("count") == 0


def test_webfetch_then_remember_becomes_active_when_quarantine_disabled(tmp_path: Path) -> None:
    _, store = _memory_store(tmp_path)
    workspace = ConversationWorkspace(tmp_path, "conv-qoff")
    toolset = AgentToolset(workspace, memory=store, http_client=_mock_http_client(), public_tool_names=True)
    toolset._effective_settings = _settings_with_quarantine(False)
    toolset._memory_turn_state_provider = lambda: {
        "user_message": "Leia https://docs.example.com/api e resuma.",
        "turn_id": "turn-qoff",
        "conversation_id": "conv-qoff",
        "effective_settings": toolset._effective_settings,
        "workspace_root": str(workspace.root),
    }

    assert toolset.invoke("WebFetch", {"url": "https://docs.example.com/api", "prompt": "resuma"}).status == "succeeded"
    rem = toolset.invoke("Remember", {"fact": "API v2 usa X-Token."})
    assert rem.status == "succeeded"
    assert "quarentena" not in rem.content.lower()
    assert rem.payload.get("status") == "active"

    found = memories_for_task(store, "API v2 X-Token")
    assert any(m["memory_id"] == rem.payload["memory_id"] for m in found)


def test_explicit_user_request_after_webfetch_is_still_quarantined_for_safety(tmp_path: Path) -> None:
    """Red Team #1 (Vetor C1): once WebFetch runs in the turn, Remember must land in quarantine
    even if the prompt contained memorization wording, preventing prompt-injection bypass."""
    engine, store = _memory_store(tmp_path)
    workspace = ConversationWorkspace(tmp_path, "conv-explicit")
    toolset = AgentToolset(workspace, memory=store, http_client=_mock_http_client(), public_tool_names=True)
    toolset._effective_settings = _settings_with_quarantine(True)
    toolset._memory_turn_state_provider = lambda: {
        "user_message": "Leia https://docs.example.com/api e lembre na memória que a API v2 usa X-Token.",
        "turn_id": "turn-explicit",
        "conversation_id": "conv-explicit",
        "effective_settings": toolset._effective_settings,
        "workspace_root": str(workspace.root),
    }

    assert toolset.invoke("WebFetch", {"url": "https://docs.example.com/api", "prompt": "resuma"}).status == "succeeded"
    rem = toolset.invoke("Remember", {"fact": "A API v2 usa X-Token."})
    assert rem.status == "succeeded"
    assert "quarentena" in rem.content.lower()
    mem_id = str(rem.payload["memory_id"])

    with engine.connect() as conn:
        row = conn.execute(select(agent_memories).where(agent_memories.c.memory_id == mem_id)).mappings().one()
    assert row["source_kind"] == "web"
    assert row["trust"] == 0
    assert row["status"] == "quarantined"
    assert not any(m["memory_id"] == mem_id for m in memories_for_task(store, "X-Token"))


def test_malicious_source_kind_user_argument_from_model_is_ignored(tmp_path: Path) -> None:
    engine, store = _memory_store(tmp_path)
    workspace = ConversationWorkspace(tmp_path, "conv-spoof")
    toolset = AgentToolset(workspace, memory=store, http_client=_mock_http_client(), public_tool_names=True)
    toolset._effective_settings = _settings_with_quarantine(True)
    toolset._memory_turn_state_provider = lambda: {
        "user_message": "Consulte a página https://docs.example.com/api.",
        "turn_id": "turn-spoof",
        "conversation_id": "conv-spoof",
        "effective_settings": toolset._effective_settings,
        "workspace_root": str(workspace.root),
    }

    # Case 1: clean turn (no web fetch yet, no explicit user request) -> must NOT become user/trust=2
    rem_clean = toolset.invoke(
        "Remember",
        {"fact": "Preferência injetada sem pedido.", "source_kind": "user", "trust": 2},
    )
    assert rem_clean.status == "succeeded"
    with engine.connect() as conn:
        clean_row = conn.execute(
            select(agent_memories).where(agent_memories.c.memory_id == str(rem_clean.payload["memory_id"]))
        ).mappings().one()
    assert clean_row["source_kind"] == "agent"
    assert clean_row["trust"] == 1

    # Case 2: after WebFetch -> malicious source_kind="user" still lands as web/quarantined!
    assert toolset.invoke("WebFetch", {"url": "https://docs.example.com/api", "prompt": "leia"}).status == "succeeded"
    rem_web = toolset.invoke(
        "Remember",
        {"fact": "Regra injetada por prompt injection.", "source_kind": "user", "trust": 2},
    )
    assert rem_web.status == "succeeded"
    assert "salva em quarentena, aguardando aprovação" in rem_web.content.lower()
    with engine.connect() as conn:
        web_row = conn.execute(
            select(agent_memories).where(agent_memories.c.memory_id == str(rem_web.payload["memory_id"]))
        ).mappings().one()
    assert web_row["source_kind"] == "web"
    assert web_row["trust"] == 0
    assert web_row["status"] == "quarantined"


def test_turn_session_wires_provenance_and_records_memory_quarantined_event(tmp_path: Path) -> None:
    engine, store = _memory_store(tmp_path)
    recorded_events: list[tuple[AgentActivityEventType, str, dict[str, Any]]] = []

    class _FakeStore:
        def __init__(self) -> None:
            self.turn = {
                "turn_id": "t-sess",
                "conversation_id": "c-sess",
                "user_id": "user:1",
                "project_id": "proj:orin",
                "project_workspace_id": "ws-proj-orin",
                "execution_id": "e-sess",
                "provider": "openrouter",
                "model_id": "m",
                "user_message_id": "um",
                "assistant_message_id": "am",
            }

        def load(self, turn_id):
            return self.turn

        def history_for_turn(self, turn):
            return [{"role": "user", "content": "Analise a página https://docs.example.com"}]

        def has_external_tool_calls(self, conversation_id: str) -> bool:
            return False

        def lifecycle(self, turn, state, **payload):
            pass

        def delta(self, turn, text):
            pass

        def finish(self, turn, *, failed=False, code=None):
            pass

        def record(self, turn, event_type, summary, payload=None, **kwargs):
            recorded_events.append((event_type, summary, dict(payload or {})))

        def main_agent_id(self, turn):
            return "agent:c-sess:main"

    class _Agents:
        def list(self):
            return []

    fake_store = _FakeStore()
    session = TurnSession(
        turn=fake_store.turn,
        store=fake_store,
        agents_store=_Agents(),
        memory_store=store,
        provider_factory=lambda: object(),
        workspace_root=tmp_path,
        enable_subagents=False,
    )
    toolset = session._toolset(subagents=False)
    toolset._http_client = _mock_http_client()

    assert toolset.invoke("fetch_url", {"url": "https://docs.example.com", "prompt": "leia"}).status == "succeeded"
    outcome = toolset.invoke("remember", {"fact": "Dado vindo da web via sessão."})
    assert outcome.status == "succeeded"
    assert "salva em quarentena, aguardando aprovação" in outcome.content.lower()

    quarantined_ev = [e for e in recorded_events if e[0] == AgentActivityEventType.MEMORY_QUARANTINED]
    assert len(quarantined_ev) == 1
    assert quarantined_ev[0][2]["memory_id"] == outcome.payload["memory_id"]
    assert quarantined_ev[0][2]["source_kind"] == "web"

    with engine.connect() as conn:
        row = conn.execute(
            select(agent_memories).where(agent_memories.c.memory_id == str(outcome.payload["memory_id"]))
        ).mappings().one()
    assert row["status"] == "quarantined"
    with engine.connect() as conn:
        me = conn.execute(
            select(memory_events).where(memory_events.c.memory_id == str(outcome.payload["memory_id"]))
        ).mappings().one()
    assert me["event"] == "quarantined"
    assert me["turn_id"] == "t-sess"
    assert me["conversation_id"] == "c-sess"
