"""``ChatApplication`` composes the durable store with the (best-effort)
execution projection. See README: "Execution records are a technical
projection of a turn. A failure to write one never changes the answer the
user sees."
"""
from sqlalchemy import create_engine

from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.schema import metadata
from agentos.provider_catalog.models import ProviderCatalogContext

ATTACHMENT = {
    "path": "uploads/nota.pdf", "original_name": "nota.pdf",
    "media_type": "application/pdf", "kind": "pdf", "bytes": 2048,
}


class _RaisingExecutions:
    """Stands in for the execution port when its write fails."""

    def create(self, command):
        raise RuntimeError("execution store unavailable")


def _store() -> PostgresChatStore:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def test_create_returns_a_receipt_even_when_the_execution_projection_fails() -> None:
    """Break caught: a failing execution write must not fail the turn.

    ``PostgresChatStore.create`` already committed the conversation, the
    messages, the attachment rows, the turn and the ``pending`` dispatch row
    in one transaction. A worker can claim that dispatch within
    milliseconds. If ``_ensure_execution`` were allowed to raise, the
    exception would reach the gateway route, which deletes the promoted
    attachment files on any exception — orphaning a turn that is already
    live and already eligible for a worker.
    """
    store = _store()
    application = ChatApplication(store, _RaisingExecutions())

    receipt = application.create(
        ProviderCatalogContext("user-1", "conversation.create"),
        message="veja isto", provider="anthropic", model_id="model-a",
        workspace_id=None, idempotency_key="key-1", attachments=[ATTACHMENT],
    )

    assert receipt.state == "queued"
    assert receipt.conversation_id


def test_create_leaves_the_promoted_attachment_record_intact_after_a_failed_projection() -> None:
    store = _store()
    application = ChatApplication(store, _RaisingExecutions())

    receipt = application.create(
        ProviderCatalogContext("user-1", "conversation.create"),
        message="veja isto", provider="anthropic", model_id="model-a",
        workspace_id=None, idempotency_key="key-2", attachments=[ATTACHMENT],
    )

    turn = store.claim(receipt.turn_id)
    assert store.attachments_for_turn(turn) == [ATTACHMENT]


def test_send_returns_a_receipt_even_when_the_execution_projection_fails() -> None:
    store = _store()
    application = ChatApplication(store, _RaisingExecutions())
    first = application.create(
        ProviderCatalogContext("user-1", "conversation.create"),
        message="abrir conversa", provider="anthropic", model_id="model-a",
        workspace_id=None, idempotency_key="key-3",
    )

    receipt = application.send(user_id="user-1", conversation_id=first.conversation_id, message="segunda mensagem", idempotency_key="key-4")

    assert receipt.state == "queued"
    assert receipt.turn_id != first.turn_id
