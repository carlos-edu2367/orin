from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from agentos.filesystem.persistence import FilesystemPersistenceJournal
from agentos.persistence.postgres import PostgresTransactionalPersistence, upgrade


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


def test_filesystem_resource_fact_commits_with_outbox_in_postgresql() -> None:
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    suffix = uuid4().hex
    result = FilesystemPersistenceJournal(PostgresTransactionalPersistence(engine)).record_fact(
        user_id=f"user:{suffix}", workspace_id=f"workspace:{suffix}", agent_id=f"agent:{suffix}", execution_id=f"execution:{suffix}", correlation_id=f"correlation:{suffix}", purpose="filesystem.write", actor="integration", operation_id=f"operation:{suffix}", event_type="FilesystemWriteFinished", outcome="SUCCEEDED", version=1,
    )

    assert result.__class__.__name__ == "TransactionCommitted"
    assert len(result.receipt.outbox_refs) == 1
