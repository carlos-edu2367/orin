from datetime import datetime, timedelta, timezone

import pytest

from agentos.artifact_storage import (
    AccessPurpose,
    ArtifactCategory,
    ArtifactGrant,
    ArtifactMetadata,
    ArtifactNamespace,
    ArtifactOperationContext,
    ArtifactProvenance,
    ArtifactReference,
    ArtifactState,
    ChecksumAlgorithm,
    ContentChecksum,
    DataClassification,
    OpaqueArtifactRef,
    OpaqueReadRef,
    OpaqueWriteSessionRef,
)


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


def context(**overrides):
    values = {
        "user_id": "user:1",
        "workspace_id": "workspace:1",
        "agent_id": "agent:1",
        "execution_id": "execution:1",
        "correlation_id": "correlation:1",
        "purpose": AccessPurpose("artifact.write"),
        "actor": "actor:1",
    }
    values.update(overrides)
    return ArtifactOperationContext(**values)


def metadata(**overrides):
    values = {
        "artifact_id": "artifact:1",
        "namespace": ArtifactNamespace("ns:user:1:workspace:1:result"),
        "logical_name": "result.json",
        "category": ArtifactCategory.RESULT,
        "media_type": "application/json",
        "declared_media_type": "application/json",
        "size_bytes": 4,
        "checksum": ContentChecksum(ChecksumAlgorithm.SHA256, "a" * 64),
        "classification": DataClassification.INTERNAL,
        "provenance": ArtifactProvenance("AGENT_RESULT", ("execution:1",), context()),
        "retention_policy_ref": "retention:short",
        "state": ArtifactState.AVAILABLE,
        "version": 1,
        "created_at": NOW,
        "available_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
    }
    values.update(overrides)
    return ArtifactMetadata(**values)


def test_context_namespace_checksum_and_metadata_are_bounded_and_immutable():
    operation = context()
    namespace = ArtifactNamespace("partition:opaque")
    checksum = ContentChecksum(ChecksumAlgorithm.SHA256, "b" * 64)
    artifact = metadata(namespace=namespace, checksum=checksum)

    with pytest.raises(Exception):
        operation.purpose = AccessPurpose("changed")
    assert artifact.state is ArtifactState.AVAILABLE
    assert artifact.namespace is namespace
    assert checksum.algorithm is ChecksumAlgorithm.SHA256

    with pytest.raises(ValueError):
        ArtifactNamespace("../physical/path")
    with pytest.raises(ValueError):
        ContentChecksum(ChecksumAlgorithm.SHA256, "short")
    with pytest.raises(ValueError):
        metadata(size_bytes=-1)


def test_references_and_handles_never_represent_content_or_physical_location():
    reference = ArtifactReference(
        artifact_id="artifact:1",
        version=1,
        user_id="user:1",
        workspace_id="workspace:1",
        category=ArtifactCategory.RESULT,
        size_bytes=4,
        checksum=ContentChecksum(ChecksumAlgorithm.SHA256, "a" * 64),
        classification=DataClassification.INTERNAL,
        authorization_ref="grant:1",
        purpose=AccessPurpose("artifact.read"),
        expires_at=None,
    )
    handles = (
        OpaqueArtifactRef("native:/secret/path"),
        OpaqueReadRef("bucket/key"),
        OpaqueWriteSessionRef("provider-handle"),
    )

    assert "native:/secret/path" not in repr(handles[0])
    assert "bucket/key" not in repr(handles[1])
    assert "provider-handle" not in repr(handles[2])
    assert not hasattr(reference, "bytes")
    assert not hasattr(reference, "path")


def test_grant_binds_scope_version_purpose_and_classification():
    grant = ArtifactGrant(
        grant_id="grant:1",
        artifact_id="artifact:1",
        user_id="user:1",
        workspace_id="workspace:1",
        agent_id="agent:1",
        execution_id="execution:1",
        purpose=AccessPurpose("artifact.read"),
        classification_ceiling=DataClassification.CONFIDENTIAL,
        version=1,
        expires_at=NOW + timedelta(minutes=5),
        revoked_at=None,
    )

    assert grant.allows(context(purpose=AccessPurpose("artifact.read")), version=1, classification=DataClassification.INTERNAL, now=NOW)
    assert not grant.allows(context(purpose=AccessPurpose("artifact.other")), version=1, classification=DataClassification.INTERNAL, now=NOW)
    assert not grant.allows(context(user_id="user:2"), version=1, classification=DataClassification.INTERNAL, now=NOW)
