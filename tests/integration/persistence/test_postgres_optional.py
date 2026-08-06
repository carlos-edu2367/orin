import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from agentos.events.models import DataClassification, EventEnvelope
from agentos.persistence import (
    ExpectedVersion,
    OutboxChange,
    PersistenceOperationContext,
    RecordChange,
    RecordReference,
    TransactionOptions,
    TransactionRequest,
)
from agentos.persistence.postgres.adapter import PostgresTransactionalPersistence
from agentos.persistence.postgres.migrate import upgrade


pytestmark = pytest.mark.skipif(
    not os.environ.get("AGENTOS_TEST_POSTGRES_DSN"),
    reason="AGENTOS_TEST_POSTGRES_DSN is not configured",
)


def test_postgres_rejects_stale_optimistic_version_without_overwrite():
    dsn = os.environ["AGENTOS_TEST_POSTGRES_DSN"]
    engine = create_engine(dsn, future=True)
    upgrade(engine)
    adapter = PostgresTransactionalPersistence(engine)
    suffix = uuid4().hex
    context = PersistenceOperationContext(
        user_id=f"test-user:{suffix}",
        workspace_id=f"test-workspace:{suffix}",
        agent_id=f"test-agent:{suffix}",
        execution_id=f"test-execution:{suffix}",
        correlation_id=f"test-correlation:{suffix}",
        purpose="test.persistence",
        actor="test-actor",
    )
    reference = RecordReference(context.execution_id)
    adapter.seed(
        __import__("agentos.persistence", fromlist=["AuthorizedRecord"]).AuthorizedRecord(
            record_ref=reference,
            record_type="execution",
            version=1,
            context=context,
            classification=DataClassification.INTERNAL,
            data={"state": "QUEUED"},
        )
    )
    event = EventEnvelope(
        event_id=f"test-event:{suffix}",
        event_type="ExecutionStarted",
        event_version=1,
        occurred_at=datetime.now(timezone.utc),
        source="test",
        correlation_id=context.correlation_id,
        causation_id=None,
        sequence=2,
        user_id=context.user_id,
        workspace_id=context.workspace_id,
        agent_id=context.agent_id,
        execution_id=context.execution_id,
        classification=DataClassification.INTERNAL,
        payload={"state": "RUNNING"},
    )
    request = TransactionRequest(
        transaction_id=f"test-transaction:{suffix}",
        context=context,
        options=TransactionOptions(),
        idempotency_key=f"test-idempotency:{suffix}",
        fingerprint="test-fingerprint",
        expected_versions=(ExpectedVersion(reference, 1),),
        changes=(RecordChange(reference, "execution", 1, {"state": "RUNNING"}, DataClassification.INTERNAL),),
        audit=(),
        outbox=(OutboxChange(event, reference, 2),),
    )

    first = adapter.transact(request)
    stale_event = EventEnvelope(
        event_id=f"test-event-stale:{suffix}",
        event_type="ExecutionStarted",
        event_version=1,
        occurred_at=datetime.now(timezone.utc),
        source="test",
        correlation_id=context.correlation_id,
        causation_id=None,
        sequence=2,
        user_id=context.user_id,
        workspace_id=context.workspace_id,
        agent_id=context.agent_id,
        execution_id=context.execution_id,
        classification=DataClassification.INTERNAL,
        payload={"state": "STALE"},
    )
    stale = adapter.transact(
        TransactionRequest(
            transaction_id=f"test-transaction-stale:{suffix}",
            context=context,
            options=TransactionOptions(),
            idempotency_key=f"test-idempotency-stale:{suffix}",
            fingerprint="test-fingerprint-stale",
            expected_versions=(ExpectedVersion(reference, 1),),
            changes=(RecordChange(reference, "execution", 1, {"state": "STALE"}, DataClassification.INTERNAL),),
            audit=(),
            outbox=(OutboxChange(stale_event, reference, 2),),
        )
    )

    assert first.__class__.__name__ == "TransactionCommitted"
    assert stale.__class__.__name__ == "TransactionConflicted"
