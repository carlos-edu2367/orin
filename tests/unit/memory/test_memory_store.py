from datetime import datetime, timezone

import pytest

from agentos.events import DataClassification, EventEnvelope
from agentos.memory.in_memory import InMemoryMemoryStore
from agentos.memory.models import (
    BoundedMemoryContent,
    MemoryAuditRecord,
    MemoryCommitChange,
    MemoryCommitRequest,
    MemoryIdempotencyConflict,
    MemoryOperation,
    MemoryOperationContext,
    MemoryProvenance,
    MemoryRecord,
    MemoryRevision,
    MemoryScope,
    MemoryStatus,
    MemoryVersionConflict,
    MemoryWriteReceipt,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def ctx():
    return MemoryOperationContext(
        user_id="user-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        execution_id="execution-1",
        correlation_id="correlation-1",
        purpose="memory.write",
        actor="agent-1",
    )


def record(version=1, status=MemoryStatus.ACTIVE, invalidated_at=None):
    provenance = MemoryProvenance(
        source_kind="USER_STATEMENT",
        source_refs=("source:1",),
        integrity_ref="integrity:1",
    )
    return MemoryRecord(
        memory_id="memory:1",
        user_id="user-1",
        workspace_id="workspace-1",
        owner_agent_id="agent-1",
        scope=MemoryScope.PRIVATE,
        base_scope=MemoryScope.PRIVATE,
        kind="FACT",
        content=BoundedMemoryContent(f"fact:{version}"),
        provenance=provenance,
        classification=DataClassification.INTERNAL,
        retention_policy_ref="retention:1",
        status=status,
        version=version,
        created_by="agent-1",
        created_execution_id="execution-1",
        correlation_id="correlation-1",
        created_at=NOW,
        valid_from=NOW,
        invalidated_at=invalidated_at,
    )


def request(*, new_record, expected_version, key="idem:1", fingerprint="fingerprint:1", event_id="event:1", sequence=1):
    context = ctx()
    revision = MemoryRevision(
        memory_id=new_record.memory_id,
        version=new_record.version,
        previous_version=expected_version,
        changed_by=context.actor,
        execution_id=context.execution_id,
        correlation_id=context.correlation_id,
        change_reason="test",
        changed_at=NOW,
    )
    audit = MemoryAuditRecord(
        audit_id=f"audit:{event_id}",
        operation=MemoryOperation.SAVE,
        context=context,
        outcome="COMMITTED",
        memory_ids=(str(new_record.memory_id),),
        versions=(int(new_record.version),),
        scope=new_record.scope,
        reason="test",
        event_id=event_id,
    )
    event = EventEnvelope(
        event_id=event_id,
        event_type="MemorySaved",
        event_version=1,
        occurred_at=NOW,
        source="memory",
        correlation_id=context.correlation_id,
        causation_id=None,
        sequence=sequence,
        user_id=context.user_id,
        workspace_id=context.workspace_id,
        agent_id=context.agent_id,
        execution_id=context.execution_id,
        classification=DataClassification.INTERNAL,
        payload={"memory_id": str(new_record.memory_id), "version": int(new_record.version), "scope": "PRIVATE"},
    )
    return MemoryCommitRequest(
        operation=MemoryOperation.SAVE,
        context=context,
        idempotency_key=key,
        fingerprint=fingerprint,
        changes=(MemoryCommitChange(new_record, expected_version, revision),),
        audit=audit,
        event=event,
        result=MemoryWriteReceipt(
            memory_id=new_record.memory_id,
            version=new_record.version,
            status=new_record.status,
            correlation_id=context.correlation_id,
            event_id=event_id,
        ),
    )


def test_store_commits_record_revision_audit_and_outbox_together():
    store = InMemoryMemoryStore()
    result = store.commit(request(new_record=record(), expected_version=None))

    assert result.applied is True
    assert tuple(item.memory_id for item in store.records) == ("memory:1",)
    assert len(store.revisions) == 1
    assert len(store.audit_log) == 1
    assert tuple(item.event_id for item in store.outbox) == ("event:1",)


def test_store_rejects_stale_version_without_partial_effects():
    store = InMemoryMemoryStore()
    store.commit(request(new_record=record(), expected_version=None))
    before = (store.records, store.revisions, store.audit_log, store.outbox)

    with pytest.raises(MemoryVersionConflict):
        store.commit(request(new_record=record(version=2), expected_version=2, key="idem:2", fingerprint="fingerprint:2", event_id="event:2", sequence=2))

    assert (store.records, store.revisions, store.audit_log, store.outbox) == before


def test_store_replays_same_idempotency_and_rejects_divergent_fingerprint():
    store = InMemoryMemoryStore()
    first = store.commit(request(new_record=record(), expected_version=None))
    replay = store.commit(request(new_record=record(), expected_version=None))

    assert first.result == replay.result
    assert replay.already_applied is True
    assert len(store.records) == len(store.audit_log) == len(store.outbox) == 1

    with pytest.raises(MemoryIdempotencyConflict):
        store.commit(request(new_record=record(), expected_version=None, fingerprint="different"))


def test_store_keeps_terminal_tombstone_and_does_not_resurrect_it():
    store = InMemoryMemoryStore()
    store.commit(request(new_record=record(), expected_version=None))
    terminal = record(version=2, status=MemoryStatus.INVALIDATED, invalidated_at=NOW)
    store.commit(request(new_record=terminal, expected_version=1, key="idem:2", fingerprint="fingerprint:2", event_id="event:2", sequence=2))

    assert store.get("memory:1").status is MemoryStatus.INVALIDATED
    with pytest.raises(MemoryVersionConflict):
        store.commit(request(new_record=record(version=3), expected_version=2, key="idem:3", fingerprint="fingerprint:3", event_id="event:3", sequence=3))
