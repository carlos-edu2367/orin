from dataclasses import replace
from datetime import datetime, timezone

from agentos.events.models import DataClassification, EventEnvelope
from agentos.persistence import (
    AuditChange,
    AuthorizedRecord,
    CommitState,
    ExpectedVersion,
    InspectCommit,
    NotFound,
    OutboxChange,
    PersistenceErrorCode,
    PersistenceOperationContext,
    RecordChange,
    RecordReference,
    TransactionCommitted,
    TransactionConflicted,
    TransactionIndeterminate,
    TransactionOptions,
    TransactionRejected,
    TransactionRequest,
)


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


def operation_context(**overrides):
    values = {
        "user_id": "user:1",
        "workspace_id": "workspace:1",
        "agent_id": "agent:1",
        "execution_id": "execution:1",
        "correlation_id": "correlation:1",
        "purpose": "execution.persist",
        "actor": "actor:1",
    }
    values.update(overrides)
    return PersistenceOperationContext(**values)


def event(*, event_id="event:1", sequence=2):
    ctx = operation_context()
    return EventEnvelope(
        event_id=event_id,
        event_type="ExecutionStarted",
        event_version=1,
        occurred_at=NOW,
        source="execution-control",
        correlation_id=ctx.correlation_id,
        causation_id="command:1",
        sequence=sequence,
        user_id=ctx.user_id,
        workspace_id=ctx.workspace_id,
        execution_id=ctx.execution_id,
        agent_id=ctx.agent_id,
        classification=DataClassification.INTERNAL,
        payload={"state_version": sequence},
    )


def seed_record():
    from agentos.persistence.in_memory import InMemoryTransactionalPersistence

    ctx = operation_context()
    store = InMemoryTransactionalPersistence()
    store.seed(
        AuthorizedRecord(
            record_ref=RecordReference(ctx.execution_id),
            record_type="execution",
            version=1,
            context=ctx,
            classification=DataClassification.INTERNAL,
            data={"state": "QUEUED", "state_version": 1},
        )
    )
    return store, ctx


def request(ctx, *, fingerprint="fingerprint:1", expected_version=1, event_id="event:1"):
    record_ref = RecordReference(ctx.execution_id)
    return TransactionRequest(
        transaction_id="transaction:1",
        context=ctx,
        options=TransactionOptions(),
        idempotency_key="idempotency:1",
        fingerprint=fingerprint,
        expected_versions=(ExpectedVersion(record_ref, expected_version),),
        changes=(
            RecordChange(
                record_ref=record_ref,
                record_type="execution",
                expected_version=expected_version,
                data={"state": "RUNNING", "state_version": expected_version + 1},
                classification=DataClassification.INTERNAL,
            ),
        ),
        audit=(
            AuditChange(
                audit_ref="audit:1",
                record_ref=record_ref,
                decision="APPLIED",
                resulting_version=expected_version + 1,
                fields={"operation": "transition"},
            ),
        ),
        outbox=(
            OutboxChange(
                event=event(event_id=event_id, sequence=expected_version + 1),
                source_record_ref=record_ref,
                expected_source_version=expected_version + 1,
            ),
        ),
    )


def test_in_memory_transaction_commits_state_audit_and_outbox_once():
    store, ctx = seed_record()
    command = request(ctx)

    result = store.transact(command)

    assert isinstance(result, TransactionCommitted)
    assert result.receipt.commit_state is CommitState.COMMITTED
    assert result.records[0].version == 2
    assert len(store.audit_records) == 1
    assert len(store.confirmed_outbox()) == 1

    repeated = store.transact(command)

    assert isinstance(repeated, TransactionCommitted)
    assert repeated.already_applied is True
    assert len(store.audit_records) == 1
    assert len(store.confirmed_outbox()) == 1


def test_same_idempotency_key_with_different_fingerprint_is_rejected():
    store, ctx = seed_record()
    store.transact(request(ctx))

    result = store.transact(replace(request(ctx), fingerprint="fingerprint:other"))

    assert isinstance(result, TransactionRejected)
    assert result.code is PersistenceErrorCode.IDEMPOTENCY_CONFLICT


def test_version_conflict_does_not_mutate_any_transaction_side_effect():
    store, ctx = seed_record()

    result = store.transact(request(ctx, expected_version=2, event_id="event:conflict"))

    assert isinstance(result, TransactionConflicted)
    assert result.conflicts[0].actual_version == 1
    assert len(store.audit_records) == 0
    assert len(store.confirmed_outbox()) == 0
    current = store.read(
        __import__("agentos.persistence", fromlist=["AuthorizedRead"]).AuthorizedRead(
            context=ctx,
            record_ref=RecordReference(ctx.execution_id),
            record_type="execution",
            classification_ceiling=DataClassification.INTERNAL,
        )
    )
    assert not isinstance(current, NotFound)
    assert current.version == 1


def test_duplicate_outbox_event_is_rejected_without_partial_record_update():
    store, ctx = seed_record()
    store.transact(request(ctx))
    second = replace(
        request(ctx, expected_version=2, event_id="event:1"),
        transaction_id="transaction:2",
        idempotency_key="idempotency:2",
    )

    result = store.transact(second)

    assert isinstance(result, TransactionRejected)
    assert result.code is PersistenceErrorCode.DUPLICATE_OUTBOX_EVENT
    assert len(store.audit_records) == 1


def test_indeterminate_commit_is_inspected_before_retry():
    store, ctx = seed_record()
    store.indeterminate_next()

    result = store.transact(request(ctx))

    assert isinstance(result, TransactionIndeterminate)
    receipt = store.inspect_commit(
        InspectCommit(
            context=ctx,
            transaction_id=result.transaction_id,
            idempotency_key="idempotency:1",
        )
    )
    assert receipt.commit_state is CommitState.COMMITTED


def test_not_committed_has_no_visible_effect():
    store, ctx = seed_record()
    store.not_committed_next()

    result = store.transact(request(ctx))

    assert isinstance(result, TransactionRejected)
    assert result.receipt is not None
    assert result.receipt.commit_state is CommitState.NOT_COMMITTED
    assert len(store.audit_records) == 0
    assert len(store.confirmed_outbox()) == 0


def test_inspecting_an_unknown_commit_returns_not_committed_without_leaking_state():
    store, ctx = seed_record()

    receipt = store.inspect_commit(
        InspectCommit(
            context=ctx,
            transaction_id="transaction:unknown",
            idempotency_key="idempotency:unknown",
        )
    )

    assert receipt.commit_state is CommitState.NOT_COMMITTED
    assert receipt.transaction_id == "transaction:unknown"


def test_read_only_noop_does_not_advance_store_revision():
    store, ctx = seed_record()
    command = TransactionRequest(
        transaction_id="transaction:read-only",
        context=ctx,
        options=TransactionOptions(read_only=True),
        idempotency_key="idempotency:read-only",
        fingerprint="fingerprint:read-only",
        expected_versions=(),
        changes=(),
        audit=(),
        outbox=(),
    )

    result = store.transact(command)

    assert isinstance(result, TransactionCommitted)
    assert result.receipt.store_revision == 1


def test_in_memory_rejects_eventual_consistency_without_replica_support():
    store, ctx = seed_record()
    command = TransactionRequest(
        transaction_id="transaction:eventual",
        context=ctx,
        options=TransactionOptions(consistency="EVENTUAL"),
        idempotency_key="idempotency:eventual",
        fingerprint="fingerprint:eventual",
        expected_versions=(),
        changes=(),
        audit=(),
        outbox=(),
    )

    result = store.transact(command)

    assert isinstance(result, TransactionRejected)
    assert result.code is PersistenceErrorCode.INVALID_REQUEST


def test_commit_inspection_hides_records_above_classification_ceiling():
    store, ctx = seed_record()
    result = store.transact(
        TransactionRequest(
            transaction_id="transaction:restricted",
            context=ctx,
            options=TransactionOptions(),
            idempotency_key="idempotency:restricted",
            fingerprint="fingerprint:restricted",
            expected_versions=(ExpectedVersion(RecordReference(ctx.execution_id), 1),),
            changes=(
                RecordChange(
                    record_ref=RecordReference(ctx.execution_id),
                    record_type="execution",
                    expected_version=1,
                    data={"state": "RUNNING"},
                    classification=DataClassification.RESTRICTED,
                ),
            ),
            audit=(),
            outbox=(),
        )
    )

    assert isinstance(result, TransactionCommitted)
    receipt = store.inspect_commit(
        InspectCommit(
            context=ctx,
            transaction_id="transaction:restricted",
            idempotency_key="idempotency:restricted",
            classification_ceiling=DataClassification.CONFIDENTIAL,
        )
    )

    assert receipt.record_refs == (RecordReference(ctx.execution_id),)
    assert receipt.records == ()
