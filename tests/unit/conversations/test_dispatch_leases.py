from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, update

from agentos.conversations.chat import PostgresChatStore, _execution_duration_limit
from agentos.persistence.postgres.schema import conversation_dispatches, metadata
from agentos.workers import publisher
from agentos.workers.publisher import ConcurrentTurnRunner, renew_leases


def _store() -> PostgresChatStore:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def test_a_renewed_lease_is_not_stale() -> None:
    store = _store()
    receipt = store.create(user_id="u", message="hi", provider="openrouter", model_id="m", idempotency_key="k1")
    assert store.claim(receipt.turn_id) is not None
    with store._engine.begin() as connection:
        connection.execute(update(conversation_dispatches).where(conversation_dispatches.c.turn_id == receipt.turn_id).values(
            acquired_at=datetime.now(UTC) - timedelta(hours=1),
        ))
    assert [row["turn_id"] for row in store.stale_turns(maximum_age=timedelta(minutes=10))] == [receipt.turn_id]
    assert store.renew_dispatch_leases([receipt.turn_id]) == 1
    assert store.stale_turns(maximum_age=timedelta(minutes=10)) == ()


def test_renewing_nothing_is_a_no_op() -> None:
    assert _store().renew_dispatch_leases([]) == 0


def test_code_turns_get_a_long_execution_deadline() -> None:
    assert _execution_duration_limit(True) == 86_400
    assert _execution_duration_limit(False) == 3_600


def test_code_turns_retry_with_backoff_and_chat_turns_keep_one_immediate_retry() -> None:
    from agentos.workers.chat import _retry_limits

    assert _retry_limits({"code_mode": "code"}) == {"max_provider_retries": 8, "retry_base_delay_seconds": 0.5}
    assert _retry_limits({}) == {"max_provider_retries": 1, "retry_base_delay_seconds": 0.0}


class _FakeStore:
    def __init__(self) -> None:
        self.renewed: list[tuple[str, ...]] = []

    def renew_dispatch_leases(self, turn_ids):
        self.renewed.append(tuple(turn_ids))
        return len(turn_ids)

    def pending(self):
        return ("turn-a",)

    def heartbeat(self, component):
        pass


class _BlockingWorker:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.release = threading.Event()
        self.started = threading.Event()

    def run(self, turn_id):
        self.started.set()
        self.release.wait(10)


def test_in_flight_turns_have_their_leases_renewed_on_each_sweep(monkeypatch) -> None:
    worker = _BlockingWorker()
    runner = ConcurrentTurnRunner(worker, max_workers=2)  # type: ignore[arg-type]
    try:
        monkeypatch.setattr(publisher, "recover_once", lambda store, has_in_flight=False: ())
        assert runner.submit_pending() == ("turn-a",)
        assert worker.started.wait(5)
        assert runner.in_flight_ids() == ("turn-a",)
        renew_leases(runner)
        assert worker.store.renewed[-1] == ("turn-a",)
        worker.store.renewed.clear()
        worker.store.pending = lambda: ()  # nothing new to submit, so the tick reaches the sweep
        publisher.poll_once(runner, 0.0)
        assert worker.store.renewed == [("turn-a",)]
    finally:
        worker.release.set()
        runner.shutdown(wait=True)
