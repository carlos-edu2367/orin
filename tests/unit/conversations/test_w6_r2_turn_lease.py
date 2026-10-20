"""W6-R2: a CLI turn's lease exists from the moment the turn is queued; an interface turn gets none."""
from __future__ import annotations

from sqlalchemy import create_engine

from agentos.agentic import cli_lease
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata


def _store() -> PostgresChatStore:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def test_a_cli_turn_is_leased_when_it_is_queued() -> None:
    store = _store()
    receipt = store.create(
        user_id="user-1", message="oi", provider="openrouter", model_id="m", idempotency_key="cli-1",
        cli_options="{}",
    )
    assert (cli_lease.leases_root() / receipt.turn_id).is_file()
    assert cli_lease.expired(receipt.turn_id) is False


def test_an_interface_turn_gets_no_lease() -> None:
    store = _store()
    receipt = store.create(user_id="user-1", message="oi", provider="openrouter", model_id="m", idempotency_key="chat-1")
    assert not (cli_lease.leases_root() / receipt.turn_id).exists()
