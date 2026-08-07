from datetime import datetime, timezone

from agentos.artifact_storage import ArtifactCategory, ArtifactNamespace, ArtifactOperationContext, ArtifactState, DataClassification
from agentos.artifact_storage.metadata import ArtifactMetadataRecord, QuotaPolicy
from agentos.artifact_storage.persistence import TransactionalArtifactMetadataRepository
from agentos.artifact_storage.in_memory import InMemoryArtifactStorage
from agentos.artifact_storage.service import ArtifactManagerService, InMemoryArtifactEventSink
from agentos.artifact_storage.ports import BeginArtifactWrite, FinalizeArtifactWrite
from agentos.artifact_storage.models import ArtifactProvenance, ArtifactProvenanceKind
from io import BytesIO
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
    assert [record.event.event_type for record in persistence.confirmed_outbox()] == ["ArtifactWriteStarted"]


def test_transactional_repository_puts_event_and_metadata_in_same_confirmed_outbox_unit():
    ctx = context()
    persistence = InMemoryTransactionalPersistence()
    repo = TransactionalArtifactMetadataRepository(persistence, clock=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc), quota=QuotaPolicy(100, 100))
    record = repo.new_record(ctx, artifact_id="artifact:1", namespace=ArtifactNamespace("artifact-ns-test"), category=ArtifactCategory.RESULT, logical_name="result.json", classification=DataClassification.INTERNAL, storage_object_ref="opaque-object:1")

    repo.create_staging(record, reservation_bytes=4, idempotency_key="idem:1")
    published = repo.publish_available(ctx, "artifact:1", size_bytes=4, checksum=__import__("agentos.artifact_storage", fromlist=["ContentChecksum", "ChecksumAlgorithm"]).ContentChecksum(__import__("agentos.artifact_storage", fromlist=["ChecksumAlgorithm"]).ChecksumAlgorithm.SHA256, "b" * 64), idempotency_key="idem:2")

    assert published.metadata.state is ArtifactState.AVAILABLE
    assert [record.event.event_type for record in persistence.confirmed_outbox()] == ["ArtifactWriteStarted", "ArtifactStored"]
    assert all("hello" not in repr(record.event.payload) and "path" not in repr(record.event.payload) and "password" not in repr(record.event.payload) for record in persistence.confirmed_outbox())


def test_transactional_metadata_can_be_loaded_with_a_different_operation_purpose():
    ctx = context()
    persistence = InMemoryTransactionalPersistence()
    repo = TransactionalArtifactMetadataRepository(persistence, clock=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc), quota=QuotaPolicy(100, 100))
    record = repo.new_record(ctx, artifact_id="artifact:purpose", namespace=ArtifactNamespace("artifact-ns-test"), category=ArtifactCategory.RESULT, logical_name="result.json", classification=DataClassification.INTERNAL, storage_object_ref="opaque-object:1")
    repo.create_staging(record, reservation_bytes=4, idempotency_key="idem:purpose")

    read_context = ctx.__class__(ctx.user_id, ctx.workspace_id, ctx.agent_id, ctx.execution_id, ctx.correlation_id, "artifact.read", ctx.actor)
    fresh_repo = TransactionalArtifactMetadataRepository(persistence, clock=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc), quota=QuotaPolicy(100, 100))

    assert fresh_repo.get(read_context, "artifact:purpose") is not None


def test_manager_with_transactional_metadata_uses_confirmed_rfc601_outbox():
    ctx = context()
    persistence = InMemoryTransactionalPersistence()
    repo = TransactionalArtifactMetadataRepository(persistence, clock=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc), quota=QuotaPolicy(100, 100))
    service = ArtifactManagerService(InMemoryArtifactStorage(clock=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc)), repo, clock=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc), event_sink=InMemoryArtifactEventSink())
    request = BeginArtifactWrite("begin", ctx, ArtifactCategory.RESULT, "result.json", None, 0, None, DataClassification.INTERNAL, "retention", ArtifactProvenance(ArtifactProvenanceKind.AGENT_RESULT, (), ctx), "idem:manager")

    session = service.begin_write(request)
    service.finalize(FinalizeArtifactWrite("finish", ctx, session.write_session_id, 0, None, "idem:finish"))

    assert service.event_sink.events == []
    assert [record.event.event_type for record in persistence.confirmed_outbox()] == ["ArtifactWriteStarted", "ArtifactStored"]
