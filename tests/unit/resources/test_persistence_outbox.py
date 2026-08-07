from __future__ import annotations

from agentos.persistence.in_memory import InMemoryTransactionalPersistence
from agentos.persistence.models import CommitState
from agentos.resources.persistence import ResourcePersistenceJournal


def test_resource_journal_round_trips_snapshot_and_outbox_without_live_handle() -> None:
    store = InMemoryTransactionalPersistence()
    journal = ResourcePersistenceJournal(store)
    result = journal.record_fact(user_id="u", workspace_id="ws", agent_id="a", execution_id="e", correlation_id="c", purpose="resource.acquire", actor="agent:a", lease_id="lease", resource_type="FILESYSTEM", state="LEASED", outcome="APPLIED")
    assert result.receipt.commit_state is CommitState.COMMITTED
    record = result.records[0]
    assert "handle" not in repr(record.data).lower()
    assert "path" not in repr(record.data).lower()
