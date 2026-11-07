"""Phase 3 of specialised agents: a person's own agents, stored per Orin user (`/v1/custom-agents`)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.agentic.agent_definitions import AgentCatalog, AgentDefinition
from agentos.agentic.session import TurnSession
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata, provider_configurations, provider_model_catalog
from agentos.persistence.postgres.user_agents import UserAgentStore
from tests.unit.agentic.test_parallel_and_background_subagents import TURN, _RecordingStore

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
AUTH = {"Authorization": "Bearer pat-1"}
OTHER = {"Authorization": "Bearer pat-2"}


def _seed(engine) -> None:
    config = {"enabled": True, "secret_ref": "sec", "created_at": NOW, "updated_at": NOW, "base_url": None}
    model = {"display_name": "x", "context_window": 1, "capabilities": [], "input_modalities": ["text"],
             "output_modalities": ["text"], "route_kind": "model", "refreshed_at": NOW, "created_at": NOW, "updated_at": NOW}
    with engine.begin() as conn:
        conn.execute(provider_configurations.insert(), [
            {**config, "user_id": "user-1", "provider": "openai-codex", "model": "gpt-5"},
            {**config, "user_id": "user-1", "provider": "google-antigravity", "model": "gemini-3-pro"},
            {**config, "user_id": "user-1", "provider": "ollama", "model": "qwen3", "enabled": False},
        ])
        conn.execute(provider_model_catalog.insert(), [
            {**model, "user_id": "user-1", "provider": "openai-codex", "model_id": "gpt-5-codex"},
            {**model, "user_id": "user-1", "provider": "openai-codex", "model_id": "gpt-5"},
            {**model, "user_id": "user-1", "provider": "google-antigravity", "model_id": "gemini-3-pro"},
        ])


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    _seed(engine)
    security = InMemorySecurityService()
    security.add_pat("pat-1", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    security.add_pat("pat-2", AuthenticatedPrincipal("user-2", "credential-2", frozenset({"api"})))
    client = TestClient(create_app(ApiServices(security=security, conversation_application=PostgresChatStore(engine))))
    yield client, engine
    engine.dispose()


REVISOR = {
    "name": "Revisor", "description": "Revisa o diff antes do commit", "prompt": "Revise o diff e aponte bugs.",
    "provider": "openai-codex", "models": ["gpt-5-codex", "gpt-5"], "tools": ["Read", "Grep"], "max_turns": 12,
}


def _problems(response) -> list[str]:
    return response.json()["error"]["details"]["problems"]


def test_create_list_update_and_delete_an_agent(env) -> None:
    client, _engine = env
    created = client.post("/v1/custom-agents", json=REVISOR, headers=AUTH)
    assert created.status_code == 201, created.text
    agent = created.json()["agent"]
    assert agent["name"] == "revisor"  # names are lowercase
    assert (agent["provider"], agent["models"], agent["tools"], agent["enabled"]) == (
        "openai-codex", ["gpt-5-codex", "gpt-5"], ["Read", "Grep"], True,
    )
    listed = client.get("/v1/custom-agents", headers=AUTH).json()
    assert [item["name"] for item in listed["agents"]] == ["revisor"]

    body = {key: value for key, value in REVISOR.items() if key != "name"}
    updated = client.put("/v1/custom-agents/revisor", json={**body, "models": ["gpt-5"], "enabled": False}, headers=AUTH)
    assert updated.status_code == 200, updated.text
    assert (updated.json()["agent"]["models"], updated.json()["agent"]["enabled"]) == (["gpt-5"], False)

    assert client.delete("/v1/custom-agents/revisor", headers=AUTH).status_code == 200
    assert client.get("/v1/custom-agents", headers=AUTH).json()["agents"] == []
    assert client.delete("/v1/custom-agents/revisor", headers=AUTH).status_code == 404


def test_agents_belong_to_their_orin_user(env) -> None:
    client, _engine = env
    assert client.post("/v1/custom-agents", json=REVISOR, headers=AUTH).status_code == 201
    assert client.get("/v1/custom-agents", headers=OTHER).json()["agents"] == []
    body = {key: value for key, value in REVISOR.items() if key != "name"}
    assert client.put("/v1/custom-agents/revisor", json=body, headers=OTHER).status_code == 404
    assert client.delete("/v1/custom-agents/revisor", headers=OTHER).status_code == 404


def test_a_second_agent_with_the_same_name_is_refused(env) -> None:
    client, _engine = env
    assert client.post("/v1/custom-agents", json=REVISOR, headers=AUTH).status_code == 201
    again = client.post("/v1/custom-agents", json=REVISOR, headers=AUTH)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "agent_exists"


@pytest.mark.parametrize(("change", "expected"), [
    ({"name": "explore"}, "not a builtin agent"),
    ({"name": "Com Espaço"}, "lowercase letters"),
    ({"tools": ["Read; rm -rf"]}, "is not a tool name"),
    ({"provider": "ollama", "models": ["qwen3"]}, "not enabled for you"),
    ({"provider": "google-antigravity", "models": ["gemini-3-pro"]}, "not enabled for you"),  # risk not acknowledged
    ({"models": ["gpt-6"]}, "not in the catalog of openai-codex"),
    ({"models": []}, "needs at least one model"),
    ({"provider": None}, "choose the provider"),
])
def test_what_the_form_can_get_wrong_is_refused_with_the_reason(env, change: dict, expected: str) -> None:
    client, _engine = env
    response = client.post("/v1/custom-agents", json={**REVISOR, **change}, headers=AUTH)
    assert response.status_code == 422, response.text
    assert any(expected in problem for problem in _problems(response)), _problems(response)
    assert client.get("/v1/custom-agents", headers=AUTH).json()["agents"] == []


def test_an_agent_without_provider_or_models_runs_on_the_parents_model(env) -> None:
    client, _engine = env
    response = client.post("/v1/custom-agents", json={**REVISOR, "provider": None, "models": []}, headers=AUTH)
    assert response.status_code == 201, response.text
    assert (response.json()["agent"]["provider"], response.json()["agent"]["models"]) == (None, [])


# -- a turn reads them -------------------------------------------------------------------------------------------


class _ChatStore:
    def __init__(self, engine) -> None:
        self._engine = engine


def _turn_catalog(tmp_path: Path, engine, files: dict[str, AgentDefinition] | None = None) -> AgentCatalog:
    session = TurnSession(
        turn=dict(TURN, user_id="user-1"), store=_RecordingStore(), agents_store=None, memory_store=None,
        provider_factory=lambda: None, workspace_root=tmp_path, chat_store=_ChatStore(engine),
    )
    import agentos.agentic.agent_definitions as definitions

    original = definitions.load_agent_definitions
    definitions.load_agent_definitions = lambda **_kw: AgentCatalog(agents=dict(files or {}))
    try:
        return session._load_agent_definitions(trusted=True)
    finally:
        definitions.load_agent_definitions = original


def test_a_turn_reads_the_persons_enabled_agents(tmp_path: Path, env) -> None:
    client, engine = env
    assert client.post("/v1/custom-agents", json=REVISOR, headers=AUTH).status_code == 201
    assert client.post("/v1/custom-agents", json={**REVISOR, "name": "desligado", "enabled": False}, headers=AUTH).status_code == 201
    catalog = _turn_catalog(tmp_path, engine)
    agent = catalog.agents["revisor"]
    assert (agent.origin, agent.provider, agent.models, agent.tools, agent.max_turns) == (
        "orin", "openai-codex", ("gpt-5-codex", "gpt-5"), ("Read", "Grep"), 12,
    )
    assert "desligado" not in catalog.agents


def test_a_repository_agent_keeps_its_name_and_the_persons_agent_replaces_their_own_file(tmp_path: Path, env) -> None:
    client, engine = env
    assert client.post("/v1/custom-agents", json=REVISOR, headers=AUTH).status_code == 201
    project = AgentDefinition(name="revisor", description="do repo", prompt="x", origin="project")
    assert _turn_catalog(tmp_path, engine, {"revisor": project}).agents["revisor"].origin == "project"
    personal_file = AgentDefinition(name="revisor", description="do arquivo", prompt="x", origin="user")
    assert _turn_catalog(tmp_path, engine, {"revisor": personal_file}).agents["revisor"].origin == "orin"


def test_a_mention_of_a_defined_agent_tells_the_model_to_delegate(tmp_path: Path) -> None:
    from agentos.agentic.reminders import ReminderQueue

    session = TurnSession(
        turn=dict(TURN), store=_RecordingStore(), agents_store=None, memory_store=None,
        provider_factory=lambda: None, workspace_root=tmp_path,
    )
    session._reminders = ReminderQueue()
    session._agent_definitions = AgentCatalog(agents={"revisor": AgentDefinition(
        name="revisor", description="Revisa", prompt="x", origin="orin", provider="openai-codex", models=("gpt-5-codex", "gpt-5"),
    )})
    session._apply_agent_mentions("use o @revisor no src/@main.py e fale com dev@empresa.com e @desconhecido")
    reminders = session._reminders.drain()
    assert len(reminders) == 1, "only a name that is a defined agent counts"
    assert 'run_subagent(subagent_type="revisor")' in reminders[0].text
    assert "gpt-5-codex, gpt-5" in reminders[0].text


def test_the_store_keeps_rows_per_user(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'a.db').as_posix()}", future=True)
    metadata.create_all(engine)
    store = UserAgentStore(engine)
    store.create("user-1", "a", {"description": "d", "prompt": "p"})
    store.create("user-2", "a", {"description": "d2", "prompt": "p2"})
    assert [item["description"] for item in store.list("user-1")] == ["d"]
    assert store.definitions("user-2")[0].description == "d2"
    engine.dispose()
