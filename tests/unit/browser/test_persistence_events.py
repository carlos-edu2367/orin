from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentos.browser.models import BrowserOperationContext, BrowserSessionSnapshot, BrowserSessionStatus
from agentos.browser.persistence import BrowserPersistenceJournal, sanitized_event_payload
from agentos.persistence.in_memory import InMemoryTransactionalPersistence


def context():
    return BrowserOperationContext("u", "ws", "a", "e", "c", "browser.session", "agent:a")


def test_journal_round_trip_contains_only_sanitized_snapshot_and_outbox() -> None:
    persistence = InMemoryTransactionalPersistence()
    journal = BrowserPersistenceJournal(persistence)
    now = datetime.now(timezone.utc)
    snapshot = BrowserSessionSnapshot("s", "p", context(), "lease", "worker", BrowserSessionStatus.READY, (), 1, now, now + timedelta(minutes=1), None)
    result = journal.record(snapshot, context(), "operation-1", "BrowserOpened")
    assert result.commit_state.value == "COMMITTED"
    loaded = journal.load(context(), "s")
    assert loaded == snapshot
    payload = sanitized_event_payload("BrowserOpened", snapshot)
    assert "cookie" not in repr(payload).lower()
    assert "handle" not in repr(payload).lower()
