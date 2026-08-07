from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentos.filesystem.models import WorkspacePath
from agentos.persistence.in_memory import InMemoryTransactionalPersistence
from agentos.terminal.models import BufferTruncation, TerminalBuffer, TerminalOperationContext, TerminalSessionSnapshot, TerminalSessionStatus
from agentos.terminal.persistence import TerminalPersistenceJournal
from agentos.terminal.reference import ReferenceTerminalAdapter
from agentos.terminal.service import TerminalService


def context() -> TerminalOperationContext:
    return TerminalOperationContext("u", "ws", "a", "e", "c", "terminal.session", "agent:a")


def snapshot() -> TerminalSessionSnapshot:
    now = datetime.now(timezone.utc)
    return TerminalSessionSnapshot("session-1", WorkspacePath.from_string("src"), None, TerminalSessionStatus.READY, "u", "ws", "a", "e", "c", "terminal.session", TerminalBuffer(1, 2, 4, 3, 4, BufferTruncation.HEAD_DROPPED), "lease-1", None, 1, now, now, now + timedelta(minutes=5))


def test_terminal_journal_round_trips_only_sanitized_snapshot_and_outbox() -> None:
    persistence = InMemoryTransactionalPersistence()
    journal = TerminalPersistenceJournal(persistence)
    expected = snapshot()
    result = journal.record(expected, context=context(), operation_id="op-1", event_type="TerminalSessionCreated")
    assert result.commit_state.value == "COMMITTED"
    records = persistence.confirmed_outbox()
    assert len(records) == 1
    data = persistence.read(journal.authorized_read(context(), "session-1"))
    assert data.data["cwd"] == "src"
    assert "command" not in data.data
    assert "secret" not in repr(data)
    assert journal.load(context(), "session-1") == expected


def test_indeterminate_commit_is_inspected_not_retried() -> None:
    persistence = InMemoryTransactionalPersistence()
    persistence.indeterminate_next()
    journal = TerminalPersistenceJournal(persistence)
    result = journal.record(snapshot(), context=context(), operation_id="op-1", event_type="TerminalSessionCreated")
    assert result.commit_state.value == "COMMITTED"
    assert len(persistence.confirmed_outbox()) == 1


def test_service_can_restore_a_bounded_snapshot_after_restart() -> None:
    persistence = InMemoryTransactionalPersistence()
    journal = TerminalPersistenceJournal(persistence)
    expected = snapshot()
    journal.record(expected, context=context(), operation_id="op-1", event_type="TerminalSessionCreated")
    service = TerminalService(adapter=ReferenceTerminalAdapter(), persistence_journal=journal)
    restored = service.restore(context(), "session-1")
    assert restored == expected
