from __future__ import annotations

from agentos.events.models import DataClassification
from agentos.filesystem.persistence import FilesystemPersistenceJournal
from agentos.persistence.in_memory import InMemoryTransactionalPersistence
from agentos.persistence.models import CommitState


def test_filesystem_journal_persists_bounded_snapshot_and_outbox_after_commit() -> None:
    store = InMemoryTransactionalPersistence()
    journal = FilesystemPersistenceJournal(store)
    result = journal.record_fact(
        user_id="u", workspace_id="ws", agent_id="a", execution_id="e", correlation_id="c", purpose="filesystem.write", actor="agent:a", operation_id="op", event_type="FilesystemEntryCreated", outcome="APPLIED", version=1,
    )
    assert result.receipt.commit_state is CommitState.COMMITTED
    assert len(store.confirmed_outbox()) == 1
    event = next(iter(store.confirmed_outbox())).event
    assert "path" not in repr(event.payload).lower()
    assert "handle" not in repr(event.payload).lower()
