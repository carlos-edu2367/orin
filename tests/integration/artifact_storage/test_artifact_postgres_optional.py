import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from agentos.artifact_storage import ArtifactCategory, ArtifactNamespace, ArtifactOperationContext, DataClassification
from agentos.artifact_storage.metadata import QuotaPolicy
from agentos.artifact_storage.persistence import TransactionalArtifactMetadataRepository
from agentos.persistence.postgres import PostgresTransactionalPersistence, upgrade

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGENTOS_TEST_POSTGRES_DSN"),
    reason="AGENTOS_TEST_POSTGRES_DSN is not configured",
)


def test_artifact_metadata_postgres_round_trip_is_explicitly_optional():
    """The generic RFC 601 schema is the metadata adapter's PostgreSQL boundary."""
    suffix = uuid4().hex
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    persistence = PostgresTransactionalPersistence(engine)
    context = ArtifactOperationContext(f"artifact-user:{suffix}", f"artifact-workspace:{suffix}", f"artifact-agent:{suffix}", f"artifact-execution:{suffix}", f"artifact-correlation:{suffix}", "artifact.write", "artifact-test")
    repo = TransactionalArtifactMetadataRepository(persistence, clock=lambda: datetime.now(timezone.utc), quota=QuotaPolicy(100, 100))
    record = repo.new_record(context, artifact_id=f"artifact:{suffix}", namespace=ArtifactNamespace("artifact-ns-test"), category=ArtifactCategory.RESULT, logical_name="result.json", classification=DataClassification.INTERNAL, storage_object_ref="opaque-object:1")

    created = repo.create_staging(record, reservation_bytes=4, idempotency_key=f"artifact-create:{suffix}")
    loaded = TransactionalArtifactMetadataRepository(persistence, clock=lambda: datetime.now(timezone.utc), quota=QuotaPolicy(100, 100)).get(context, record.metadata.artifact_id)

    assert created.metadata.artifact_id == loaded.metadata.artifact_id
