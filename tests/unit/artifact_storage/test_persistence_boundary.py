from datetime import datetime, timezone

from agentos.artifact_storage import ArtifactCategory, ArtifactNamespace, ArtifactOperationContext, ArtifactState, DataClassification
from agentos.artifact_storage.metadata import ArtifactMetadataRecord, QuotaPolicy
from agentos.artifact_storage.persistence import TransactionalArtifactMetadataRepository
from agentos.persistence import InMemoryTransactionalPersistence


def context():
    return ArtifactOperationContext("user:1", "workspace:1", "agent:1", "execution:1", "correlation:1", "artifact.write", "actor:1")


def test_transactional_repository_uses_rfc601_without_storing_bytes():
    ctx = context()
    persistence = InMemoryTransactionalPersistence()
    repo = TransactionalArtifactMetadataRepository(persistence, clock=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc), quota=QuotaPolicy(max_staging_bytes=100, max_available_bytes=100))
    record = repo.new_record(ctx, artifact_id="artifact:1", namespace=ArtifactNamespace("artifact-ns-test"), category=ArtifactCategory.RESULT, logical_name="result.json", classification=DataClassification.INTERNAL, storage_object_ref="opaque-object:1")

    result = repo.create_staging(record, reservation_bytes=4, idempotency_key="idem:1")
    loaded = repo.get(ctx, "artifact:1")

    assert result.metadata.state is ArtifactState.STAGING
    assert loaded is not None
    assert "bytes" not in repr(loaded)
    assert len(persistence.audit_records) == 1
