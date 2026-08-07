from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import OperationalError
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
    TransactionReceipt,
    TransactionRequest,
)
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.adapter import PostgresTransactionalPersistence
from agentos.persistence.postgres.adapter import PersistenceAdapterError


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


def test_sqlalchemy_idempotency_scope_includes_correlation_and_workspace_null_is_normalized():
    adapter, ctx = make_adapter()
    adapter.transact(make_request(ctx))

    other_context = context(
        workspace_id=None,
        execution_id="execution:2",
        correlation_id="correlation:2",
    )
    adapter.seed(
        AuthorizedRecord(
            record_ref=RecordReference(other_context.execution_id),
            record_type="execution",
            version=1,
            context=other_context,
            classification=DataClassification.INTERNAL,
            data={"state": "QUEUED", "state_version": 1},
        )
    )

    result = adapter.transact(make_request(other_context, event_id="event:2"))

    assert isinstance(result, TransactionCommitted)
    assert result.already_applied is False


def test_sqlalchemy_idempotent_replay_returns_original_record_snapshot():
    adapter, ctx = make_adapter()
    first = make_request(ctx)
    adapter.transact(first)
    adapter.transact(
        make_request(
            ctx,
            transaction_id="transaction:2",
            key="idempotency:2",
            expected=2,
            event_id="event:2",
            fingerprint="fingerprint:2",
        )
    )

    repeated = adapter.transact(first)

    assert isinstance(repeated, TransactionCommitted)
    assert repeated.already_applied is True
    assert repeated.records[0].version == 2
    assert repeated.records[0].data["state_version"] == 2


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


def test_sqlalchemy_rejects_duplicate_record_changes_before_mutation():
    adapter, ctx = make_adapter()
    original = make_request(ctx)
    duplicate = replace(original, changes=(original.changes[0], original.changes[0]), outbox=())

    result = adapter.transact(duplicate)

    assert isinstance(result, TransactionRejected)
    assert result.code is PersistenceErrorCode.INVALID_REQUEST
    assert read_current(adapter, ctx).version == 1


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

    key_only = adapter.inspect_commit(
        InspectCommit(context=ctx, transaction_id=None, idempotency_key=request.idempotency_key)
    )
    assert key_only.fingerprint == request.fingerprint
    assert key_only.records[0].version == 2


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


def test_sqlalchemy_scan_is_bounded_and_cursor_is_bound_to_page_shape():
    adapter, ctx = make_adapter()
    for index in (2, 3):
        adapter.seed(
            AuthorizedRecord(
                record_ref=RecordReference(f"execution:{index}"),
                record_type="execution",
                version=1,
                context=ctx,
                classification=DataClassification.INTERNAL,
                data={"state": "QUEUED", "state_version": 1},
            )
        )

    first = adapter.scan(
        AuthorizedScan(
            context=ctx,
            record_type="execution",
            filters={"state": "QUEUED"},
            classification_ceiling=DataClassification.INTERNAL,
            page=__import__("agentos.persistence", fromlist=["PageRequest"]).PageRequest(limit=1),
        )
    )
    assert len(first.items) == 1
    assert first.next_cursor is not None

    second = adapter.scan(
        AuthorizedScan(
            context=ctx,
            record_type="execution",
            filters={"state": "QUEUED"},
            classification_ceiling=DataClassification.INTERNAL,
            page=__import__("agentos.persistence", fromlist=["PageRequest"]).PageRequest(
                limit=1, cursor=first.next_cursor
            ),
        )
    )
    assert len(second.items) == 1
    assert second.items[0].record_ref != first.items[0].record_ref

    with __import__("pytest").raises(ValueError, match="invalid persistence cursor"):
        adapter.scan(
            AuthorizedScan(
                context=ctx,
                record_type="execution",
                filters={"state": "QUEUED"},
                classification_ceiling=DataClassification.INTERNAL,
                page=__import__("agentos.persistence", fromlist=["PageRequest"]).PageRequest(
                    limit=2, cursor=first.next_cursor
                ),
            )
        )


def test_sqlalchemy_applies_postgres_transaction_options():
    adapter = PostgresTransactionalPersistence.__new__(PostgresTransactionalPersistence)
    adapter.engine = Mock()
    adapter.engine.dialect.name = "postgresql"
    session = Mock()
    ctx = context()
    request = make_request(ctx)
    request = replace(
        request,
        options=TransactionOptions(
            isolation="SERIALIZABLE",
            timeout=__import__("datetime").timedelta(seconds=2),
            read_only=True,
        ),
    )

    adapter._configure_transaction_options(session, request)

    session.connection.assert_called_once_with(execution_options={"isolation_level": "SERIALIZABLE"})
    assert session.execute.call_count == 2


def test_sqlalchemy_idempotency_inspection_is_actor_scoped():
    adapter, ctx = make_adapter()
    request = make_request(ctx)
    adapter.transact(request)

    from agentos.persistence import InspectCommit

    receipt = adapter.inspect_commit(
        InspectCommit(
            context=replace(ctx, actor="actor:other"),
            transaction_id=request.transaction_id,
            idempotency_key=request.idempotency_key,
        )
    )

    assert receipt.commit_state is CommitState.NOT_COMMITTED


def test_sqlalchemy_legacy_idempotency_inspection_recovers_current_authorized_record():
    adapter, ctx = make_adapter()
    receipt = TransactionReceipt(
        transaction_id="transaction:legacy",
        commit_state=CommitState.COMMITTED,
        record_refs=(RecordReference(ctx.execution_id),),
        outbox_refs=(),
        store_revision=1,
        committed_at=NOW,
    )
    from agentos.persistence.postgres.schema import persistence_idempotency

    with adapter._Session.begin() as session:
        session.execute(
            persistence_idempotency.insert().values(
                user_id=ctx.user_id,
                workspace_id=ctx.workspace_id,
                workspace_scope=ctx.workspace_id,
                agent_id=ctx.agent_id,
                execution_id=ctx.execution_id,
                correlation_id="__legacy__:1",
                purpose=ctx.purpose,
                actor=ctx.actor,
                idempotency_key="idempotency:legacy",
                fingerprint="fingerprint:legacy",
                transaction_id=receipt.transaction_id,
                commit_state=receipt.commit_state.value,
                receipt=adapter._receipt_values(receipt),
                records=[],
                store_revision=receipt.store_revision,
                created_at=NOW,
            )
        )

    inspected = adapter.inspect_commit(
        InspectCommit(context=ctx, transaction_id=None, idempotency_key="idempotency:legacy")
    )

    assert inspected.commit_state is CommitState.COMMITTED
    assert inspected.records[0].version == 1


def test_sqlalchemy_legacy_inspection_does_not_cross_correlation_scope():
    adapter, ctx = make_adapter()
    receipt = TransactionReceipt(
        transaction_id="transaction:legacy-cross",
        commit_state=CommitState.COMMITTED,
        record_refs=(RecordReference(ctx.execution_id),),
        outbox_refs=(),
        store_revision=1,
        committed_at=NOW,
    )
    from agentos.persistence.postgres.schema import persistence_idempotency

    with adapter._Session.begin() as session:
        session.execute(
            persistence_idempotency.insert().values(
                user_id=ctx.user_id,
                workspace_id=ctx.workspace_id,
                workspace_scope=ctx.workspace_id,
                agent_id=ctx.agent_id,
                execution_id=ctx.execution_id,
                correlation_id="__legacy__:cross",
                purpose=ctx.purpose,
                actor=ctx.actor,
                idempotency_key="idempotency:legacy-cross",
                fingerprint="fingerprint:legacy-cross",
                transaction_id=receipt.transaction_id,
                commit_state=receipt.commit_state.value,
                receipt=adapter._receipt_values(receipt),
                records=[],
                store_revision=receipt.store_revision,
                created_at=NOW,
            )
        )

    inspected = adapter.inspect_commit(
        InspectCommit(
            context=replace(ctx, correlation_id="correlation:other"),
            transaction_id=None,
            idempotency_key="idempotency:legacy-cross",
        )
    )

    assert inspected.commit_state is CommitState.NOT_COMMITTED


def test_sqlalchemy_rejects_mutation_in_read_only_transaction():
    adapter, ctx = make_adapter()
    request = replace(make_request(ctx), options=TransactionOptions(read_only=True))

    result = adapter.transact(request)

    assert isinstance(result, TransactionRejected)
    assert result.code is PersistenceErrorCode.INVALID_REQUEST
    assert read_current(adapter, ctx).version == 1


def test_sqlalchemy_scan_normalizes_database_failures_without_driver_details():
    adapter, ctx = make_adapter()
    adapter._Session = Mock(
        side_effect=OperationalError("SELECT secret", {}, RuntimeError("password=secret"))
    )

    with pytest.raises(PersistenceAdapterError) as raised:
        adapter.scan(
            AuthorizedScan(
                context=ctx,
                record_type="execution",
                filters={},
                classification_ceiling=DataClassification.INTERNAL,
            )
        )

    assert raised.value.code is PersistenceErrorCode.CONNECTION
    assert "secret" not in str(raised.value)
    assert "SELECT" not in str(raised.value)


def test_sqlalchemy_key_only_inspection_keeps_opaque_fallback_id_on_database_failure():
    adapter, ctx = make_adapter()
    adapter._Session = Mock(
        side_effect=OperationalError("SELECT secret", {}, RuntimeError("password=secret"))
    )

    receipt = adapter.inspect_commit(
        InspectCommit(context=ctx, transaction_id=None, idempotency_key="idempotency:missing")
    )

    assert receipt.commit_state is CommitState.UNKNOWN
    assert receipt.transaction_id == "inspection:unknown"
