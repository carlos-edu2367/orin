from datetime import datetime, timezone

from agentos.persistence import (
    CommitState,
    OutboxReference,
    PersistenceErrorCode,
    RecordReference,
    Retryability,
    TransactionIndeterminate,
    TransactionReceipt,
    TransactionRejected,
    InspectCommit,
    PersistenceOperationContext,
)
from agentos.persistence.postgres.errors import normalize_database_error


def test_public_failure_and_receipt_reprs_do_not_echo_sensitive_opaque_values():
    sensitive = "postgres://user:secret-password@db.internal/agentos"
    rejected = TransactionRejected(
        PersistenceErrorCode.CONNECTION,
        Retryability.POLICY_DEPENDENT,
        transaction_id=sensitive,
    )
    indeterminate = TransactionIndeterminate(sensitive)
    receipt = TransactionReceipt(
        transaction_id=sensitive,
        commit_state=CommitState.COMMITTED,
        record_refs=(RecordReference("record:1"),),
        outbox_refs=(OutboxReference("event:1"),),
        store_revision=1,
        committed_at=datetime.now(timezone.utc),
    )

    assert sensitive not in repr(rejected)
    assert sensitive not in repr(indeterminate)
    assert sensitive not in repr(receipt)


def test_database_error_normalization_drops_driver_sql_and_credentials():
    error = normalize_database_error(
        RuntimeError("INSERT INTO persistence_records VALUES ('secret-password')"),
    )

    assert str(error) == "persistence error: UNKNOWN"
    assert "INSERT" not in str(error)
    assert "secret-password" not in str(error)


def test_inspect_commit_repr_keeps_opaque_identifiers_out_of_logs():
    sensitive = "postgres://user:secret-password@db.internal/agentos"
    query = InspectCommit(
        context=PersistenceOperationContext(
            user_id="user:1",
            workspace_id=None,
            agent_id="agent:1",
            execution_id="execution:1",
            correlation_id="correlation:1",
            purpose="persistence.inspect",
            actor="actor:1",
        ),
        transaction_id=sensitive,
        idempotency_key=sensitive,
    )

    assert sensitive not in repr(query)
