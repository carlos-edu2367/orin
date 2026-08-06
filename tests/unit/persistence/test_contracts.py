from datetime import timedelta

import pytest

from agentos.events.models import DataClassification
from agentos.persistence import (
    AuthorizedRead,
    AuthorizedScan,
    ConsistencyLevel,
    IsolationLevel,
    PageRequest,
    PersistenceOperationContext,
    RecordChange,
    RecordReference,
    TransactionOptions,
    TransactionRequest,
)


def context(**overrides) -> PersistenceOperationContext:
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


def test_operation_context_requires_all_ownership_and_actor_fields():
    with pytest.raises(ValueError):
        context(actor=" ")

    with pytest.raises(ValueError):
        context(purpose="x" * 129)

    with pytest.raises(ValueError):
        context(execution_id="")

    with pytest.raises(ValueError):
        context(user_id="u" * 256)

    with pytest.raises(ValueError):
        context(correlation_id="c" * 256)


def test_transaction_options_are_bounded_and_typed():
    options = TransactionOptions(
        consistency=ConsistencyLevel.STRONG,
        isolation=IsolationLevel.SERIALIZABLE,
        timeout=timedelta(seconds=5),
        read_only=False,
    )

    assert options.consistency is ConsistencyLevel.STRONG
    assert options.isolation is IsolationLevel.SERIALIZABLE

    with pytest.raises(ValueError):
        TransactionOptions(timeout=timedelta(seconds=0))


def test_record_reference_and_page_cursor_represent_opaque_values():
    reference = RecordReference("secret:record-id")
    page = PageRequest(limit=10, cursor="cursor-with-private-state")

    assert "secret:record-id" not in repr(reference)
    assert "private-state" not in repr(page)

    with pytest.raises(ValueError):
        RecordReference("r" * 256)


def test_record_change_freezes_payload_and_rejects_protected_values():
    change = RecordChange(
        record_ref=RecordReference("execution:1"),
        record_type="execution",
        expected_version=None,
        data={"state": "QUEUED", "version": 1},
        classification=DataClassification.INTERNAL,
    )

    with pytest.raises(TypeError):
        change.data["state"] = "RUNNING"

    with pytest.raises(ValueError):
        RecordChange(
            record_ref=RecordReference("execution:2"),
            record_type="execution",
            expected_version=None,
            data={"password": "do-not-store"},
            classification=DataClassification.INTERNAL,
        )


def test_authorized_queries_require_bounded_context_and_page():
    read = AuthorizedRead(
        context=context(),
        record_ref=RecordReference("execution:1"),
        record_type="execution",
        classification_ceiling=DataClassification.CONFIDENTIAL,
    )
    scan = AuthorizedScan(
        context=context(),
        record_type="execution",
        filters={"state": "QUEUED"},
        classification_ceiling=DataClassification.INTERNAL,
        page=PageRequest(limit=10),
    )

    assert read.record_type == "execution"
    assert scan.page.limit == 10

    with pytest.raises(ValueError):
        PageRequest(limit=101)


def test_transaction_request_freezes_collection_inputs():
    request = TransactionRequest(
        transaction_id="transaction:1",
        context=context(),
        options=TransactionOptions(),
        idempotency_key="idempotency:1",
        fingerprint="fingerprint:1",
        expected_versions=[],
        changes=[],
        audit=[],
        outbox=[],
    )

    assert request.expected_versions == ()
    assert request.changes == ()
    assert request.audit == ()
    assert request.outbox == ()
