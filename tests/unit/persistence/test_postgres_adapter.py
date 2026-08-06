from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy import create_engine

from agentos.events.models import DataClassification
from agentos.persistence import (
    AuthorizedRead,
    AuthorizedRecord,
    AuthorizedScan,
    CommitState,
    ExpectedVersion,
    InspectCommit,
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
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.adapter import PostgresTransactionalPersistence


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


def context(**overrides):
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


def make_event(ctx, event_id="event:1", sequence=2):
    from agentos.events.models import EventEnvelope

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


def make_request(ctx, *, transaction_id="transaction:1", key="idempotency:1", expected=1, event_id="event:1", fingerprint="fingerprint:1"):
    ref = RecordReference(ctx.execution_id)
    return TransactionRequest(
        transaction_id=transaction_id,
        context=ctx,
        options=TransactionOptions(),
        idempotency_key=key,
        fingerprint=fingerprint,
        expected_versions=(ExpectedVersion(ref, expected),),
        changes=(
            RecordChange(
                record_ref=ref,
                record_type="execution",
                expected_version=expected,
                data={"state": "RUNNING", "state_version": expected + 1},
                classification=DataClassification.INTERNAL,
            ),
        ),
        audit=(),
        outbox=(
            OutboxChange(
                event=make_event(ctx, event_id=event_id, sequence=expected + 1),
                source_record_ref=ref,
                expected_source_version=expected + 1,
            ),
        ),
    )


def make_adapter(*, commit_hook=None):
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine)
    adapter = PostgresTransactionalPersistence(engine, commit_hook=commit_hook)
    ctx = context()
    adapter.seed(
        AuthorizedRecord(
            record_ref=RecordReference(ctx.execution_id),
            record_type="execution",
            version=1,
            context=ctx,
            classification=DataClassification.INTERNAL,
            data={"state": "QUEUED", "state_version": 1},
        )
    )
    return adapter, ctx


def read_current(adapter, ctx):
    return adapter.read(
        AuthorizedRead(
            context=ctx,
            record_ref=RecordReference(ctx.execution_id),
            record_type="execution",
            classification_ceiling=DataClassification.INTERNAL,
        )
    )


def test_sqlalchemy_adapter_commits_record_and_outbox_atomically():
    adapter, ctx = make_adapter()

    result = adapter.transact(make_request(ctx))

    assert isinstance(result, TransactionCommitted)
    assert result.receipt.commit_state is CommitState.COMMITTED
    assert read_current(adapter, ctx).version == 2

    repeated = adapter.transact(make_request(ctx))
    assert isinstance(repeated, TransactionCommitted)
    assert repeated.already_applied is True


def test_sqlalchemy_adapter_rejects_fingerprint_conflict_and_version_conflict():
    adapter, ctx = make_adapter()
    adapter.transact(make_request(ctx))

    fingerprint_conflict = adapter.transact(
        make_request(ctx, fingerprint="fingerprint:other", transaction_id="transaction:2", key="idempotency:1")
    )
    version_conflict = adapter.transact(
        make_request(ctx, expected=1, transaction_id="transaction:3", key="idempotency:3", event_id="event:3")
    )

    assert isinstance(fingerprint_conflict, TransactionRejected)
    assert fingerprint_conflict.code is PersistenceErrorCode.IDEMPOTENCY_CONFLICT
    assert isinstance(version_conflict, TransactionConflicted)


def test_sqlalchemy_adapter_rolls_back_record_when_outbox_constraint_fails():
    adapter, ctx = make_adapter()
    adapter.transact(make_request(ctx))
    second = make_request(ctx, expected=2, transaction_id="transaction:2", key="idempotency:2", event_id="event:1")

    result = adapter.transact(second)

    assert isinstance(result, TransactionRejected)
    assert result.code in {
        PersistenceErrorCode.DUPLICATE_OUTBOX_EVENT,
        PersistenceErrorCode.CONSTRAINT_VIOLATION,
    }
    assert read_current(adapter, ctx).version == 2


def test_sqlalchemy_commit_ack_loss_returns_indeterminate_and_inspection_finds_commit():
    def lose_ack(_request):
        raise ConnectionError("postgres://secret-password@host/db")

    adapter, ctx = make_adapter(commit_hook=lose_ack)
    request = make_request(ctx)

    result = adapter.transact(request)

    assert isinstance(result, TransactionIndeterminate)
    receipt = adapter.inspect_commit(
        InspectCommit(context=ctx, transaction_id=request.transaction_id, idempotency_key=request.idempotency_key)
    )
    assert receipt.commit_state is CommitState.COMMITTED
    assert read_current(adapter, ctx).version == 2


def test_sqlalchemy_reads_apply_server_scope_and_classification_filters():
    adapter, ctx = make_adapter()
    adapter.seed(
        AuthorizedRecord(
            record_ref=RecordReference("execution:restricted"),
            record_type="execution",
            version=1,
            context=ctx,
            classification=DataClassification.RESTRICTED,
            data={"state": "QUEUED"},
        )
    )

    unauthorized = adapter.read(
        AuthorizedRead(
            context=context(user_id="user:other"),
            record_ref=RecordReference(ctx.execution_id),
            record_type="execution",
            classification_ceiling=DataClassification.RESTRICTED,
        )
    )
    hidden = adapter.read(
        AuthorizedRead(
            context=ctx,
            record_ref=RecordReference("execution:restricted"),
            record_type="execution",
            classification_ceiling=DataClassification.CONFIDENTIAL,
        )
    )

    from agentos.persistence import NotFound

    assert isinstance(unauthorized, NotFound)
    assert isinstance(hidden, NotFound)


def test_sqlalchemy_idempotency_inspection_is_actor_scoped():
    adapter, ctx = make_adapter()
    request = make_request(ctx)
    adapter.transact(request)

    from agentos.persistence import InspectCommit

    with __import__("pytest").raises(LookupError):
        adapter.inspect_commit(
            InspectCommit(
                context=replace(ctx, actor="actor:other"),
                transaction_id=request.transaction_id,
                idempotency_key=request.idempotency_key,
            )
        )


def test_sqlalchemy_rejects_mutation_in_read_only_transaction():
    adapter, ctx = make_adapter()
    request = replace(make_request(ctx), options=TransactionOptions(read_only=True))

    result = adapter.transact(request)

    assert isinstance(result, TransactionRejected)
    assert result.code is PersistenceErrorCode.INVALID_REQUEST
    assert read_current(adapter, ctx).version == 1
