from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api.contracts import ApplicationValidationError
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.schema import metadata


def _store() -> PostgresChatStore:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def test_a_compact_turn_is_marked_and_kept_out_of_later_history() -> None:
    store = _store()
    app = ChatApplication(store)
    receipt = store.create(user_id="u", message="hello", provider="openrouter", model_id="m", idempotency_key="k1")
    first = store.claim(receipt.turn_id)
    store.finish(first)
    compact = app.request_compaction("u", receipt.conversation_id, "focus on the API", "k2")
    turn = store.claim(compact.turn_id)
    assert turn is not None and turn["turn_kind"] == "compact"
    store.mark_command_turn(turn)
    store.finish(turn)
    later = store.claim(app.send("u", receipt.conversation_id, "next", "k3").turn_id)
    contents = [item["content"] for item in store.history_for_turn(later)]
    assert "/compact focus on the API" not in contents and contents[-1] == "next"


def test_compaction_requests_are_validated() -> None:
    store = _store()
    app = ChatApplication(store)
    receipt = store.create(user_id="u", message="hello", provider="openrouter", model_id="m", idempotency_key="k1")
    with pytest.raises(ApplicationValidationError):
        app.request_compaction("u", receipt.conversation_id, "x" * 4001, "k2")
    with pytest.raises(ValueError):
        store.create(user_id="u", message="x", provider="", model_id="", idempotency_key="k3", conversation_id=receipt.conversation_id, turn_kind="other")


class StubApp:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get(self, conversation_id, user_id):
        return {"conversation_id": conversation_id, "title": "T", "state": "completed", "provider": "openai", "model_id": "m",
                "messages": [], "turns": [], "activities": [], "activity_cursor": "0"}

    def request_compaction(self, user_id, conversation_id, instructions, idempotency_key):
        self.calls.append((user_id, conversation_id, instructions, idempotency_key))
        return {"conversation_id": conversation_id, "turn_id": "turn-x", "state": "queued"}


def test_the_compact_route(tmp_path: Path) -> None:
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal(user_id="local-user", credential_ref="pat-1", scopes=frozenset({"api"})))
    stub = StubApp()
    client = TestClient(create_app(ApiServices(
        security=security, conversation_application=stub,  # type: ignore[arg-type]
        agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"), workspace_root=tmp_path / "w",
    )))
    headers = {"Authorization": "Bearer pat", "Idempotency-Key": "c-1"}
    response = client.post("/v1/conversations/conv-1/compact", json={"instructions": "keep the API names"}, headers=headers)
    assert response.status_code == 202 and response.json()["turn_id"] == "turn-x"
    assert stub.calls == [("local-user", "conv-1", "keep the API names", "c-1")]
    assert client.post("/v1/conversations/conv-1/compact", json={"instructions": "x" * 4001}, headers=headers).status_code in {400, 422}
