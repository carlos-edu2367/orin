"""Phase 2 of specialised agents: an agent definition pins its provider and models."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from agentos.agentic.agent_definitions import AgentCatalog, AgentDefinition, agents_from_mapping, parse_agent_file
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.provider_stream import ProviderKeyRejected
from agentos.agentic.session import TurnSession
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata, provider_configurations, provider_model_catalog
from agentos.persistence.sqlite import create_local_engine
from agentos.workers import chat as chat_module
from agentos.workers.chat import ChatWorker
from tests.unit.agentic.test_nested_subagents import _delegate, _Scripted, _tool_names, _TreeStore
from tests.unit.agentic.test_parallel_and_background_subagents import TURN, _text_batch

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


# -- definitions -----------------------------------------------------------------------------------------------


def _file(tmp_path: Path, frontmatter: str) -> Path:
    path = tmp_path / "agent.md"
    path.write_text(f"---\nname: revisor\ndescription: Revisa codigo\n{frontmatter}---\nRevise.\n", encoding="utf-8")
    return path


def test_an_agent_file_reads_its_provider_and_models(tmp_path: Path) -> None:
    definition = parse_agent_file(_file(tmp_path, "provider: openai-codex\nmodels: [gpt-5-codex, gpt-5]\n"), origin="user")
    assert (definition.provider, definition.models) == ("openai-codex", ("gpt-5-codex", "gpt-5"))
    text = parse_agent_file(_file(tmp_path, "models: gpt-5-codex, gpt-5\n"), origin="user")
    assert (text.provider, text.models) == (None, ("gpt-5-codex", "gpt-5"))
    single = parse_agent_file(_file(tmp_path, "provider: openai-codex\nmodel: gpt-5\n"), origin="user")
    assert single.models == ("gpt-5",)
    legacy = parse_agent_file(_file(tmp_path, "model: sonnet\n"), origin="user")
    assert (legacy.provider, legacy.models, legacy.model) == (None, (), "sonnet")


@pytest.mark.parametrize("frontmatter", [
    "provider: openai-codex\n",                      # a provider and no model
    "provider: openai-codex\nmodel: inherit\n",
    "provider: Open AI!\nmodels: [gpt-5]\n",         # not a provider id
    "models: [\"gpt 5; rm\"]\n",                      # not a model id
])
def test_a_bad_provider_or_models_entry_is_rejected(tmp_path: Path, frontmatter: str) -> None:
    with pytest.raises(ValueError):
        parse_agent_file(_file(tmp_path, frontmatter), origin="user")


def test_agents_given_as_json_read_provider_and_models() -> None:
    found, errors = agents_from_mapping({
        "revisor": {"description": "Revisa", "prompt": "Revise.", "provider": "openai-codex", "models": ["gpt-5"]},
        "quebrado": {"description": "x", "prompt": "y", "provider": "openai-codex"},
    })
    assert [(item.name, item.provider, item.models) for item in found] == [("revisor", "openai-codex", ("gpt-5",))]
    assert errors and errors[0][0] == "quebrado"


# -- the session -----------------------------------------------------------------------------------------------


def _definition(provider: str | None, models: tuple[str, ...], name: str = "revisor") -> AgentDefinition:
    return AgentDefinition(name=name, description="Revisa codigo", prompt="Revise.", origin="user",
                           provider=provider, models=models)


@pytest.fixture()
def agents_store(tmp_path: Path):
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'pinned.db').as_posix()}")
    metadata.create_all(engine)
    yield ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    engine.dispose()


class _Routing:
    """A child provider factory that records where each model was routed and can refuse some of them."""

    def __init__(self, child: _Scripted, *, broken: set[str] = frozenset(), failing_stream: set[str] = frozenset()) -> None:
        self.child, self.broken, self.failing_stream = child, set(broken), set(failing_stream)
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, model_id: str, provider: str | None = None):
        self.calls.append((model_id, provider))
        if model_id in self.broken:
            raise RuntimeError(f"{model_id} cannot start")
        if model_id in self.failing_stream:
            return _RejectingProvider()
        return self.child


class _RejectingProvider:
    def stream(self, _request: dict[str, Any]):
        rejected = ProviderKeyRejected("not logged in")
        rejected.status_code = 401
        raise rejected


def _session(tmp_path: Path, agents_store, routing: _Routing, definitions: list[AgentDefinition], *,
             allowed: set[tuple[str, str]] | None = None, main: _Scripted | None = None, store=None) -> TurnSession:
    session = TurnSession(
        turn=dict(TURN), store=store or _TreeStore(), agents_store=agents_store, memory_store=None,
        provider_factory=lambda: main or _Scripted([]), workspace_root=tmp_path, child_provider_factory=routing,
        agent_model_authorizer=(lambda model_id, provider: (model_id, provider) in allowed) if allowed is not None else None,
    )
    session._agent_definitions = AgentCatalog(agents={item.name: item for item in definitions})
    return session


CODEX = {("gpt-5-codex", "openai-codex"), ("gpt-5", "openai-codex")}


def test_a_single_model_agent_runs_on_its_own_provider_and_model(tmp_path: Path, agents_store) -> None:
    routing = _Routing(_Scripted([_text_batch("revisado")]))
    session = _session(tmp_path, agents_store, routing, [_definition("openai-codex", ("gpt-5-codex",))], allowed=CODEX)
    outcome = session._run_subagent("revisor", "revise")
    assert outcome.status == "succeeded", outcome.content
    assert routing.calls == [("gpt-5-codex", "openai-codex")]
    assert routing.child.requests[0].get("model") in (None, "gpt-5-codex")
    row = next(item for item in agents_store.list() if item["name"] == "revisor")
    assert (row["provider"], row["model_id"]) == ("openai-codex", "gpt-5-codex")


def test_several_models_and_none_chosen_is_refused_with_the_list(tmp_path: Path, agents_store) -> None:
    routing = _Routing(_Scripted([]))
    session = _session(tmp_path, agents_store, routing, [_definition("openai-codex", ("gpt-5-codex", "gpt-5"))], allowed=CODEX)
    outcome = session._run_subagent("revisor", "revise")
    assert outcome.error_code == "CHILD_MODEL_REQUIRED"
    assert "gpt-5-codex" in outcome.content and "gpt-5" in outcome.content
    assert outcome.payload["available_models"] == ["gpt-5-codex", "gpt-5"]
    assert routing.calls == [] and agents_store.list() == [] and session._subagent_runs == 0


def test_the_chosen_model_must_be_one_of_the_agents(tmp_path: Path, agents_store) -> None:
    routing = _Routing(_Scripted([_text_batch("ok")]))
    session = _session(tmp_path, agents_store, routing, [_definition("openai-codex", ("gpt-5-codex", "gpt-5"))], allowed=CODEX)
    assert session._run_subagent("revisor", "revise", model_id="claude-opus-5-5").error_code == "CHILD_MODEL_NOT_ALLOWED"
    outcome = session._run_subagent("revisor", "revise", model_id="gpt-5")
    assert outcome.status == "succeeded", outcome.content
    assert routing.calls == [("gpt-5", "openai-codex")]


def test_a_model_the_person_cannot_run_now_is_refused_and_a_single_available_one_is_used(tmp_path: Path, agents_store) -> None:
    routing = _Routing(_Scripted([_text_batch("ok")]))
    only_gpt5 = {("gpt-5", "openai-codex")}
    session = _session(tmp_path, agents_store, routing, [_definition("openai-codex", ("gpt-5-codex", "gpt-5"))], allowed=only_gpt5)
    assert session._run_subagent("revisor", "revise", model_id="gpt-5-codex").error_code == "CHILD_MODEL_NOT_AVAILABLE"
    assert session._run_subagent("revisor", "revise").status == "succeeded"
    assert routing.calls == [("gpt-5", "openai-codex")]

    nothing = _session(tmp_path, agents_store, _Routing(_Scripted([])), [_definition("openai-codex", ("gpt-5",))], allowed=set())
    assert nothing._run_subagent("revisor", "revise").error_code == "CHILD_MODEL_NOT_AVAILABLE"


def test_models_without_a_provider_run_on_the_parents_provider_without_guessing(tmp_path: Path, agents_store) -> None:
    routing = _Routing(_Scripted([_text_batch("ok")]))
    session = _session(tmp_path, agents_store, routing, [_definition(None, ("~anthropic/claude-opus-4-6",))])
    # No authorizer: the turn's own provider and its current model are allowed.
    assert session._run_subagent("revisor", "revise").status == "succeeded"
    assert routing.calls == [("~anthropic/claude-opus-4-6", TURN["provider"])]


def test_a_model_that_cannot_start_falls_back_to_the_next_of_the_agents_models(tmp_path: Path, agents_store) -> None:
    store = _TreeStore()
    routing = _Routing(_Scripted([_text_batch("ok")]), broken={"gpt-5-codex"})
    session = _session(tmp_path, agents_store, routing, [_definition("openai-codex", ("gpt-5-codex", "gpt-5"))],
                       allowed=CODEX, store=store)
    outcome = session._run_subagent("revisor", "revise", model_id="gpt-5-codex")
    assert outcome.status == "succeeded", outcome.content
    assert routing.calls == [("gpt-5-codex", "openai-codex"), ("gpt-5", "openai-codex")]
    routed = [payload for kind, _a, _p, payload in store.tree_events if kind == AgentActivityEventType.MODEL_ROUTING_STARTED]
    assert routed and (routed[0]["requested_model_id"], routed[0]["fallback_model_id"]) == ("gpt-5-codex", "gpt-5")


def test_a_model_whose_stream_fails_before_acting_falls_back_within_the_list(tmp_path: Path, agents_store) -> None:
    routing = _Routing(_Scripted([_text_batch("ok")]), failing_stream={"gpt-5-codex"})
    session = _session(tmp_path, agents_store, routing, [_definition("openai-codex", ("gpt-5-codex", "gpt-5"))], allowed=CODEX)
    outcome = session._run_subagent("revisor", "revise", model_id="gpt-5-codex")
    assert outcome.status == "succeeded", outcome.content
    assert [model for model, _ in routing.calls] == ["gpt-5-codex", "gpt-5"]


@pytest.mark.parametrize("how", ["factory", "stream"])
def test_when_every_model_fails_the_agent_never_runs_on_the_parents_model(tmp_path: Path, agents_store, how: str) -> None:
    models = {"gpt-5-codex", "gpt-5"}
    routing = _Routing(_Scripted([]), broken=models if how == "factory" else set(),
                       failing_stream=models if how == "stream" else set())
    session = _session(tmp_path, agents_store, routing, [_definition("openai-codex", ("gpt-5-codex", "gpt-5"))], allowed=CODEX)
    outcome = session._run_subagent("revisor", "revise", model_id="gpt-5-codex")
    assert outcome.status == "failed"
    assert outcome.error_code == "CHILD_MODELS_UNAVAILABLE"
    assert "does not fall back" in outcome.content
    assert {model for model, _ in routing.calls} == models
    assert all(provider == "openai-codex" for _, provider in routing.calls)
    assert next(item for item in agents_store.list() if item["name"] == "revisor")["state"] == "failed"


def test_an_agent_without_pinned_models_keeps_falling_back_to_the_parents_model(tmp_path: Path, agents_store) -> None:
    routing = _Routing(_Scripted([_text_batch("ok")]), broken={"claude-haiku-4-5"})
    session = TurnSession(
        turn=dict(TURN), store=_TreeStore(), agents_store=agents_store, memory_store=None,
        provider_factory=lambda: _Scripted([]), workspace_root=tmp_path, child_provider_factory=routing,
        child_model_ids=("claude-haiku-4-5",),
    )
    outcome = session._run_subagent("general", "trabalhe", model_id="claude-haiku-4-5")
    assert outcome.status == "succeeded", outcome.content
    assert routing.calls == [("claude-haiku-4-5", None), (TURN["model_id"], None)]


def test_the_agents_reminder_shows_the_models_and_reaches_subagents_that_may_delegate(tmp_path: Path, agents_store) -> None:
    child = _Scripted([_text_batch("ok")])
    main = _Scripted([_delegate("coordene"), _text_batch("pronto")])
    routing = _Routing(child)
    session = _session(tmp_path, agents_store, routing, [_definition("openai-codex", ("gpt-5-codex", "gpt-5"))],
                       allowed=CODEX, main=main)
    text = session._defined_agents_text()
    assert "- revisor [openai-codex: gpt-5-codex | gpt-5]: Revisa codigo" in text
    assert "pass model_id" in text


    original = TurnSession._load_agent_definitions
    try:
        TurnSession._load_agent_definitions = lambda self, _trusted: AgentCatalog(agents={"revisor": _definition("openai-codex", ("gpt-5-codex", "gpt-5"))})
        runtime = session.build_runtime()
        try:
            assert runtime.run(TURN["turn_id"]).state == "completed"
        finally:
            runtime.close()
    finally:
        TurnSession._load_agent_definitions = original
    assert {"run_subagent", "Agent"} & _tool_names(child.requests[0]), "the general subagent may delegate"
    assert "revisor [openai-codex: gpt-5-codex | gpt-5]" in repr(child.requests[0]["messages"])


# -- the worker: authorization and routing by the declared provider ------------------------------------------------


@pytest.fixture()
def worker(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(chat_module, "ExecutionApplicationAdapter", lambda engine: object())
    monkeypatch.setattr(chat_module, "ExecutionQueryAdapter", lambda engine: object())
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    config = {"user_id": "user-1", "enabled": True, "secret_ref": "sec", "created_at": NOW, "updated_at": NOW}
    with engine.begin() as conn:
        conn.execute(provider_configurations.insert(), [
            {**config, "provider": "anthropic", "model": "claude-opus-5-5", "base_url": "https://anthropic.local"},
            {**config, "provider": "omniroute", "model": "claude-opus-5-5", "base_url": "https://omniroute.local"},
            {**config, "provider": "google-antigravity", "model": "gemini-3-pro", "base_url": None},
            {**config, "provider": "openai-codex", "model": "gpt-5", "base_url": None, "enabled": False},
        ])
        model = {"user_id": "user-1", "display_name": "x", "context_window": 1, "capabilities": [], "input_modalities": ["text"],
                 "output_modalities": ["text"], "route_kind": "model", "refreshed_at": NOW, "created_at": NOW, "updated_at": NOW}
        conn.execute(provider_model_catalog.insert(), [
            {**model, "provider": "anthropic", "model_id": "claude-opus-5-5"},
            {**model, "provider": "omniroute", "model_id": "claude-opus-5-5"},
            {**model, "provider": "omniroute", "model_id": "only-in-omniroute"},
            {**model, "provider": "google-antigravity", "model_id": "gemini-3-pro"},
            {**model, "provider": "openai-codex", "model_id": "gpt-5"},
        ])

    class CatalogStore:
        _engine = engine

    instance = ChatWorker(CatalogStore())
    built: list[tuple[str, str]] = []
    monkeypatch.setattr(instance, "_transport_for", lambda user_id, provider, model_id, num_ctx=None: built.append((provider, model_id)) or object())
    yield instance, built
    engine.dispose()


TURN_ON_ANTHROPIC = {"user_id": "user-1", "provider": "anthropic", "model_id": "claude-opus-5-5"}


def test_the_worker_authorizes_an_agents_model_by_its_providers_catalog_without_favorites(worker) -> None:
    chat, _built = worker
    assert chat._is_agent_model_allowed(TURN_ON_ANTHROPIC, "only-in-omniroute", "omniroute") is True
    assert chat._is_agent_model_allowed(TURN_ON_ANTHROPIC, "only-in-omniroute", "anthropic") is False
    assert chat._is_agent_model_allowed(TURN_ON_ANTHROPIC, "gpt-5", "openai-codex") is False, "disabled provider"
    # Antigravity counts only once its risk was acknowledged.
    assert chat._is_agent_model_allowed(TURN_ON_ANTHROPIC, "gemini-3-pro", "google-antigravity") is False


def test_the_worker_routes_to_the_declared_provider_even_when_the_model_exists_in_two(worker) -> None:
    chat, built = worker
    chat._child_provider_for_turn(TURN_ON_ANTHROPIC, "claude-opus-5-5", provider="omniroute")
    assert built[-1] == ("omniroute", "claude-opus-5-5")
    chat._child_provider_for_turn(TURN_ON_ANTHROPIC, "claude-opus-5-5", provider="anthropic")
    assert built[-1] == ("anthropic", "claude-opus-5-5")
    with pytest.raises(PermissionError):
        chat._child_provider_for_turn(TURN_ON_ANTHROPIC, "gpt-5", provider="openai-codex")
