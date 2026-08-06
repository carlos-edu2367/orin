from datetime import datetime, timezone

import pytest

from agentos.events.models import DataClassification
from agentos.persistence import (
    AuthorizedRead,
    AuthorizedScan,
    AuthorizedRecord,
    NotFound,
    PageRequest,
    PersistenceOperationContext,
    RecordReference,
)
from agentos.persistence.in_memory import InMemoryTransactionalPersistence


def ctx(**overrides):
    values = {
        "user_id": "user:1",
        "workspace_id": "workspace:1",
        "agent_id": "agent:1",
        "execution_id": "execution:1",
        "correlation_id": "correlation:1",
        "purpose": "execution.read",
        "actor": "actor:1",
    }
    values.update(overrides)
    return PersistenceOperationContext(**values)


def make_store():
    store = InMemoryTransactionalPersistence()
    store.seed(
        AuthorizedRecord(
            record_ref=RecordReference("record:internal"),
            record_type="execution",
            version=1,
            context=ctx(),
            classification=DataClassification.INTERNAL,
            data={"state": "QUEUED"},
        )
    )
    store.seed(
        AuthorizedRecord(
            record_ref=RecordReference("record:restricted"),
            record_type="execution",
            version=1,
            context=ctx(),
            classification=DataClassification.RESTRICTED,
            data={"state": "QUEUED"},
        )
    )
    return store


def query(context, *, ceiling=DataClassification.RESTRICTED, cursor=None):
    return AuthorizedRead(
        context=context,
        record_ref=RecordReference("record:internal"),
        record_type="execution",
        classification_ceiling=ceiling,
    )


def test_unauthorized_read_is_indistinguishable_from_missing_record():
    store = make_store()

    result = store.read(query(ctx(user_id="user:other")))

    assert isinstance(result, NotFound)
    assert repr(result) == "NotFound()"


def test_classification_above_ceiling_is_hidden():
    store = make_store()
    restricted_query = AuthorizedRead(
        context=ctx(),
        record_ref=RecordReference("record:restricted"),
        record_type="execution",
        classification_ceiling=DataClassification.CONFIDENTIAL,
    )

    assert isinstance(store.read(restricted_query), NotFound)


def test_scan_cursor_is_bound_to_context_filters_classification_and_revision():
    store = make_store()
    first = store.scan(
        AuthorizedScan(
            context=ctx(),
            record_type="execution",
            filters={},
            classification_ceiling=DataClassification.RESTRICTED,
            page=PageRequest(limit=1),
        )
    )
    assert first.next_cursor

    with pytest.raises(ValueError, match="invalid persistence cursor"):
        store.scan(
            AuthorizedScan(
                context=ctx(actor="actor:other"),
                record_type="execution",
                filters={},
                classification_ceiling=DataClassification.RESTRICTED,
                page=PageRequest(limit=1, cursor=first.next_cursor),
            )
        )

    with pytest.raises(ValueError, match="invalid persistence cursor"):
        store.scan(
            AuthorizedScan(
                context=ctx(),
                record_type="execution",
                filters={"state": "RUNNING"},
                classification_ceiling=DataClassification.RESTRICTED,
                page=PageRequest(limit=1, cursor=first.next_cursor),
            )
        )


def test_scan_applies_scope_and_classification_before_materialization():
    store = make_store()
    page = store.scan(
        AuthorizedScan(
            context=ctx(),
            record_type="execution",
            filters={},
            classification_ceiling=DataClassification.CONFIDENTIAL,
            page=PageRequest(limit=10),
        )
    )

    assert tuple(item.record_ref.value for item in page.items) == ("record:internal",)
