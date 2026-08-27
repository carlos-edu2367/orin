"""Chat turns and canonical Executions are one durable command."""
from sqlalchemy import create_engine

import pytest

from agentos.conversations import chat as chat_module
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter, ExecutionQueryAdapter
from agentos.persistence.postgres.schema import metadata, persistence_clock
from agentos.provider_catalog.models import ProviderCatalogContext

ATTACHMENT = {
    "path": "uploads/nota.pdf", "original_name": "nota.pdf",
    "media_type": "application/pdf", "kind": "pdf", "bytes": 2048,
}


def _store() -> PostgresChatStore:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(persistence_clock.insert().values(id=1, revision=0))
    return PostgresChatStore(engine)


def test_create_commits_the_turn_and_execution_together() -> None:
    store = _store()
    application = ChatApplication(store)

    receipt = application.create(
        ProviderCatalogContext("user-1", "conversation.create"),
        message="veja isto", provider="anthropic", model_id="model-a",
        workspace_id=None, idempotency_key="key-1", attachments=[ATTACHMENT],
    )

    assert receipt.state == "queued"
    assert receipt.conversation_id
    execution = ExecutionQueryAdapter(store._engine).get({
        "resource_id": PostgresChatStore.execution_id_for(receipt.turn_id),
        "user_id": "user-1", "purpose": "execution.read",
    })
    assert execution["state"] == "QUEUED"


def test_create_leaves_the_promoted_attachment_record_intact() -> None:
    store = _store()
    application = ChatApplication(store)

    receipt = application.create(
        ProviderCatalogContext("user-1", "conversation.create"),
        message="veja isto", provider="anthropic", model_id="model-a",
        workspace_id=None, idempotency_key="key-2", attachments=[ATTACHMENT],
    )

    turn = store.claim(receipt.turn_id)
    assert store.attachments_for_turn(turn) == [ATTACHMENT]


def test_send_creates_a_distinct_canonical_execution() -> None:
    store = _store()
    application = ChatApplication(store)
    first = application.create(
        ProviderCatalogContext("user-1", "conversation.create"),
        message="abrir conversa", provider="anthropic", model_id="model-a",
        workspace_id=None, idempotency_key="key-3",
    )

    receipt = application.send(user_id="user-1", conversation_id=first.conversation_id, message="segunda mensagem", idempotency_key="key-4")

    assert receipt.state == "queued"
    assert receipt.turn_id != first.turn_id
    assert PostgresChatStore.execution_id_for(receipt.turn_id) != PostgresChatStore.execution_id_for(first.turn_id)


def test_send_can_change_the_provider_and_model_for_the_conversation() -> None:
    store = _store()
    application = ChatApplication(store)
    first = application.create(
        ProviderCatalogContext("user-1", "conversation.create"),
        message="abrir conversa", provider="openrouter", model_id="model-a",
        workspace_id=None, idempotency_key="key-switch-1",
    )

    receipt = application.send(
        user_id="user-1", conversation_id=first.conversation_id, message="usar outro modelo",
        idempotency_key="key-switch-2", provider="ollama", model_id="model-b",
    )

    snapshot = store.get(first.conversation_id, "user-1")
    turn = store.claim(receipt.turn_id)
    assert snapshot["provider"] == "ollama"
    assert snapshot["model_id"] == "model-b"
    assert turn["provider"] == "ollama"
    assert turn["model_id"] == "model-b"


def test_execution_failure_rolls_back_the_entire_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store()
    real_adapter = ExecutionApplicationAdapter

    class RejectingAfterExecutionWrite:
        def __init__(self, connection):
            self._delegate = real_adapter(connection)

        def create(self, command):
            self._delegate.create(command)
            return {"outcome": "rejected"}

    monkeypatch.setattr(chat_module, "ExecutionApplicationAdapter", RejectingAfterExecutionWrite)

    with pytest.raises(RuntimeError, match="canonical execution"):
        store.create(
            user_id="user-1", message="não persistir", provider="anthropic", model_id="model-a",
            idempotency_key="rollback-key",
        )

    assert store.list("user-1")["items"] == []
    # The nested canonical persistence session joined the store transaction;
    # therefore the execution cannot survive after the transcript rolls back.
    # The generated turn id is deliberately opaque, so the absence of every
    # execution record is asserted through the owner-scoped list.
    assert ExecutionQueryAdapter(store._engine).list({"user_id": "user-1", "purpose": "execution.read"})["items"] == []


def test_cancel_transitions_the_canonical_execution_before_the_chat_projection() -> None:
    store = _store()
    application = ChatApplication(store)
    receipt = application.create(
        ProviderCatalogContext("user-1", "conversation.create"),
        message="pare", provider="anthropic", model_id="model-a",
        workspace_id=None, idempotency_key="cancel-key",
    )

    result = application.cancel(receipt.conversation_id, "user-1")

    assert result["cancelling"] == [receipt.turn_id]
    execution = ExecutionQueryAdapter(store._engine).get({
        "resource_id": PostgresChatStore.execution_id_for(receipt.turn_id),
        "user_id": "user-1", "purpose": "execution.read",
    })
    assert execution["state"] == "CANCELLED"
