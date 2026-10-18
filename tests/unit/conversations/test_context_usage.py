from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api.contracts import ApplicationNotFoundError
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import metadata


def test_memory_and_free_space_are_reported_and_add_up() -> None:
    runtime = AgenticTurnRuntime(store=object(), provider=object(), system_prompt="s" * 4000, memory_prompt_tokens=300,
                                 limits=AgenticLimits(max_context_tokens=10_000))
    usage = runtime._context_usage([{"role": "user", "content": "hi"}], [{"role": "system", "content": "s" * 4000}, {"role": "user", "content": "hi"}], [])
    assert usage["memory_tokens"] == 300
    parts = ("system_prompt_tokens", "memory_tokens", "history_tokens", "input_tokens", "tools_tokens", "skills_tokens", "mcps_tokens")
    assert usage["used_tokens"] == sum(usage[key] for key in parts)
    assert usage["free_tokens"] == usage["limit_tokens"] - usage["used_tokens"]


def _store() -> PostgresChatStore:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "test-cursor-secret"))


def test_the_latest_measurement_is_served_by_category() -> None:
    store = _store()
    receipt = store.create(user_id="u", message="hi", provider="openrouter", model_id="m", idempotency_key="k1")
    turn = store.claim(receipt.turn_id)
    assert store.context_usage(receipt.conversation_id, "u")["available"] is False
    store.record(turn, AgentActivityEventType.CONTEXT_UPDATED, "Contexto atualizado", {
        "used_tokens": 6000, "limit_tokens": 10000, "percentage": 60, "system_prompt_tokens": 2000, "memory_tokens": 500,
        "history_tokens": 2500, "input_tokens": 100, "tools_tokens": 700, "skills_tokens": 100, "mcps_tokens": 100,
        "compaction_count": 1,
    })
    store.record(turn, AgentActivityEventType.CONTEXT_UPDATED, "Compactando contexto", {"compacting": True})
    usage = store.context_usage(receipt.conversation_id, "u")
    categories = {item["key"]: item["tokens"] for item in usage["categories"]}
    assert usage["available"] is True and usage["used_tokens"] == 6000 and usage["limit_tokens"] == 10000
    assert categories == {"system_prompt": 2000, "memory": 500, "tools": 700, "mcp_tools": 100, "skills": 100, "messages": 2600, "free": 4000}
    with pytest.raises(ApplicationNotFoundError):
        store.context_usage(receipt.conversation_id, "someone-else")


def test_the_context_route(tmp_path: Path) -> None:
    class StubApp:
        def get(self, conversation_id, user_id):
            return {"conversation_id": conversation_id, "title": "T", "state": "completed", "provider": "openai", "model_id": "m",
                    "messages": [], "turns": [], "activities": [], "activity_cursor": "0"}

        def context_usage(self, conversation_id, user_id):
            return {"conversation_id": conversation_id, "available": False, "categories": []}

    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal(user_id="local-user", credential_ref="pat-1", scopes=frozenset({"api"})))
    client = TestClient(create_app(ApiServices(
        security=security, conversation_application=StubApp(),  # type: ignore[arg-type]
        agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"), workspace_root=tmp_path / "w",
    )))
    response = client.get("/v1/conversations/conv-1/context", headers={"Authorization": "Bearer pat"})
    assert response.status_code == 200 and response.json()["available"] is False
