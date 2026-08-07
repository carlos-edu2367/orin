from datetime import datetime, timedelta, timezone

from agentos.artifact_storage import (
    ArtifactCategory,
    ArtifactError,
    ArtifactErrorCode,
    ArtifactMetadata,
    ArtifactNamespace,
    ArtifactOperationContext,
    ArtifactProvenance,
    ArtifactState,
    ChecksumAlgorithm,
    ContentChecksum,
    DataClassification,
)
from agentos.artifact_storage.metadata import (
    ArtifactMetadataRecord,
    InMemoryArtifactMetadataRepository,
    QuotaPolicy,
)
from agentos.artifact_storage.models import ArtifactProvenanceKind


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


def context(**overrides):
    values = {
        "user_id": "user:1",
        "workspace_id": "workspace:1",
        "agent_id": "agent:1",
        "execution_id": "execution:1",
        "correlation_id": "correlation:1",
        "purpose": "artifact.write",
        "actor": "actor:1",
    }
    values.update(overrides)
    return ArtifactOperationContext(**values)


def metadata(state=ArtifactState.STAGING, size=0, version=1, artifact_id="artifact:1"):
    ctx = context()
    return ArtifactMetadata(
        artifact_id=artifact_id,
        namespace=ArtifactNamespace("artifact-ns-test"),
        logical_name="result.json",
        category=ArtifactCategory.RESULT,
        media_type="application/json",
        declared_media_type="application/json",
        size_bytes=size,
        checksum=ContentChecksum(ChecksumAlgorithm.SHA256, "a" * 64),
        classification=DataClassification.INTERNAL,
        provenance=ArtifactProvenance(ArtifactProvenanceKind.AGENT_RESULT, ("execution:1",), ctx),
        retention_policy_ref="retention:short",
        state=state,
        version=version,
        created_at=NOW,
        available_at=NOW if state is ArtifactState.AVAILABLE else None,
        expires_at=NOW + timedelta(hours=1),
    )


def test_metadata_repository_reserves_quota_before_staging_and_releases_on_abort():
    repo = InMemoryArtifactMetadataRepository(clock=lambda: NOW, quota=QuotaPolicy(max_staging_bytes=10, max_available_bytes=20))
    record = ArtifactMetadataRecord(metadata(), "object:1", context())

    created = repo.create_staging(record, reservation_bytes=8, idempotency_key="idem:1")
    rejected = repo.create_staging(ArtifactMetadataRecord(metadata(artifact_id="artifact:2"), "object:2", context()), reservation_bytes=3, idempotency_key="idem:2")
    released = repo.abort_staging(context(), "artifact:1", idempotency_key="idem:abort")

    assert created.metadata.state is ArtifactState.STAGING
    assert isinstance(rejected, ArtifactError)
    assert rejected.code is ArtifactErrorCode.QUOTA_EXCEEDED
    assert released.effect_state.value == "APPLIED"
    assert repo.usage(context()).staging_bytes == 0


def test_publish_reconciles_actual_size_and_is_idempotent():
    repo = InMemoryArtifactMetadataRepository(clock=lambda: NOW, quota=QuotaPolicy(max_staging_bytes=20, max_available_bytes=20))
    repo.create_staging(ArtifactMetadataRecord(metadata(), "object:1", context()), reservation_bytes=10, idempotency_key="idem:1")
    published = repo.publish_available(context(), "artifact:1", size_bytes=7, checksum=ContentChecksum(ChecksumAlgorithm.SHA256, "b" * 64), idempotency_key="idem:publish")
    repeated = repo.publish_available(context(), "artifact:1", size_bytes=7, checksum=ContentChecksum(ChecksumAlgorithm.SHA256, "b" * 64), idempotency_key="idem:publish")

    assert published.metadata.state is ArtifactState.AVAILABLE
    assert published.metadata.size_bytes == 7
    assert repeated.already_applied is True
    assert repo.usage(context()).staging_bytes == 0
    assert repo.usage(context()).available_bytes == 7


def test_repository_revalidates_ownership_and_versioned_state_transitions():
    repo = InMemoryArtifactMetadataRepository(clock=lambda: NOW, quota=QuotaPolicy(max_staging_bytes=20, max_available_bytes=20))
    repo.create_staging(ArtifactMetadataRecord(metadata(), "object:1", context()), reservation_bytes=1, idempotency_key="idem:1")

    foreign = repo.get(context(user_id="user:2"), "artifact:1")
    quarantined = repo.transition(context(), "artifact:1", ArtifactState.QUARANTINED, reason="integrity mismatch", idempotency_key="idem:q")

    assert foreign is None
    assert quarantined.metadata.state is ArtifactState.QUARANTINED
    assert quarantined.metadata.version == 2
