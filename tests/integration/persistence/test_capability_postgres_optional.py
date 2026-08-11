from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from agentos.events.models import DataClassification
from agentos.persistence import AuthorizedRead, AuthorizedRecord, PersistenceOperationContext, RecordReference
from agentos.persistence.postgres import PostgresTransactionalPersistence, upgrade


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


def test_capability_state_record_round_trips_through_postgresql() -> None:
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    suffix = uuid4().hex
    context = PersistenceOperationContext(f"user:{suffix}", f"workspace:{suffix}", f"agent:{suffix}", f"execution:{suffix}", f"correlation:{suffix}", "capability.run", "integration")
    reference = RecordReference(f"capability-run:{suffix}")
    adapter = PostgresTransactionalPersistence(engine)
    adapter.seed(AuthorizedRecord(reference, "capability_run", 1, context, DataClassification.INTERNAL, {"state": "QUEUED", "state_version": 1}))

    restored = adapter.read(AuthorizedRead(context, reference, "capability_run", DataClassification.INTERNAL))

    assert restored.data == {"state": "QUEUED", "state_version": 1}
