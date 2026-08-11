"""Recovery: no conversation may look like it is running forever."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, update

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.schema import conversation_dispatches
from agentos.workers.chat import ChatWorker
from agentos.workers.publisher import recover_once


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


@pytest.fixture()
def store() -> PostgresChatStore:
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    return PostgresChatStore(engine)


def _age_dispatch(store: PostgresChatStore, turn_id: str, *, column: str, age: timedelta) -> None:
    with store._engine.begin() as connection:
        connection.execute(
            update(conversation_dispatches)
            .where(conversation_dispatches.c.turn_id == turn_id)
            .values(**{column: datetime.now(UTC) - age})
        )


def test_a_turn_no_worker_ever_claimed_is_failed_rather_than_left_queued(store: PostgresChatStore) -> None:
    user = f"user:{uuid4().hex}"
    receipt = store.create(user_id=user, message="ninguém pega", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)
    _age_dispatch(store, receipt.turn_id, column="queued_at", age=timedelta(minutes=5))

    recover_once(store)

    snapshot = store.get(receipt.conversation_id, user)
    assert snapshot["state"] == "failed"
    # The user can try again: this is an infrastructure failure, not their doing.
    assert snapshot["messages"][1]["retryable"] is True


def test_a_turn_whose_worker_died_is_requeued_instead_of_running_forever(store: PostgresChatStore) -> None:
    user = f"user:{uuid4().hex}"
    receipt = store.create(user_id=user, message="worker morre", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)
    assert store.claim(receipt.turn_id) is not None
    _age_dispatch(store, receipt.turn_id, column="acquired_at", age=timedelta(hours=1))

    recover_once(store)

    snapshot = store.get(receipt.conversation_id, user)
    assert snapshot["state"] == "queued"
    assert receipt.turn_id in store.pending()


def test_a_recovered_turn_gets_a_fresh_queue_age_before_the_unclaimed_watchdog_runs(store: PostgresChatStore) -> None:
    user = f"user:{uuid4().hex}"
    receipt = store.create(user_id=user, message="worker morre", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)
    assert store.claim(receipt.turn_id) is not None
    _age_dispatch(store, receipt.turn_id, column="queued_at", age=timedelta(minutes=11))
    _age_dispatch(store, receipt.turn_id, column="acquired_at", age=timedelta(hours=1))

    recover_once(store)
    ChatWorker(store).watchdog(maximum_age=timedelta(seconds=45))

    assert store.get(receipt.conversation_id, user)["state"] == "queued"


def test_recovery_preserves_active_turn_before_stale_timeout(store: PostgresChatStore) -> None:
    user = f"user:{uuid4().hex}"
    receipt = store.create(user_id=user, message="ainda em andamento", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)
    assert store.claim(receipt.turn_id) is not None
    _age_dispatch(store, receipt.turn_id, column="acquired_at", age=timedelta(minutes=1))

    recover_once(store)

    assert store.get(receipt.conversation_id, user)["state"] == "running"


def test_recovery_leaves_a_healthy_running_turn_alone(store: PostgresChatStore) -> None:
    user = f"user:{uuid4().hex}"
    receipt = store.create(user_id=user, message="em andamento", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)
    assert store.claim(receipt.turn_id) is not None

    recover_once(store)

    assert store.get(receipt.conversation_id, user)["state"] == "running"


def test_recovery_does_not_resurrect_a_finished_turn(store: PostgresChatStore) -> None:
    user = f"user:{uuid4().hex}"
    receipt = store.create(user_id=user, message="terminada", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    store.finish(turn)
    _age_dispatch(store, receipt.turn_id, column="acquired_at", age=timedelta(hours=1))

    recover_once(store)

    assert store.get(receipt.conversation_id, user)["state"] == "completed"
