from datetime import datetime, timedelta, timezone
from io import BytesIO

from agentos.artifact_storage import (
    AccessPurpose,
    ArtifactCategory,
    ArtifactError,
    ArtifactErrorCode,
    ArtifactNamespace,
    ArtifactOperationContext,
    ArtifactProvenance,
    ChecksumAlgorithm,
    ContentChecksum,
    DataClassification,
    EffectState,
)
from agentos.artifact_storage.in_memory import InMemoryArtifactStorage
from agentos.artifact_storage.metadata import InMemoryArtifactMetadataRepository, QuotaPolicy
from agentos.artifact_storage.ports import (
    AbortArtifactWrite,
    AppendArtifactChunk,
    BeginArtifactWrite,
    FinalizeArtifactWrite,
)
from agentos.artifact_storage.service import ArtifactManagerService, InMemoryArtifactEventSink
from agentos.artifact_storage.models import ArtifactProvenanceKind


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


def context(**overrides):
    values = {
        "user_id": "user:1", "workspace_id": "workspace:1", "agent_id": "agent:1",
        "execution_id": "execution:1", "correlation_id": "correlation:1",
        "purpose": "artifact.write", "actor": "actor:1",
    }
    values.update(overrides)
    return ArtifactOperationContext(**values)


def checksum(data: bytes):
    import hashlib
    return ContentChecksum(ChecksumAlgorithm.SHA256, hashlib.sha256(data).hexdigest())


def write_request(ctx=None, **overrides):
    ctx = ctx or context()
    values = {
        "operation_id": "operation:begin",
        "context": ctx,
        "category": ArtifactCategory.RESULT,
        "logical_name": "result.json",
        "declared_media_type": "application/json",
        "expected_size_bytes": 11,
        "expected_checksum": checksum(b"hello world"),
        "classification": DataClassification.INTERNAL,
        "retention_policy_ref": "retention:short",
        "provenance": ArtifactProvenance(ArtifactProvenanceKind.AGENT_RESULT, (ctx.execution_id,), ctx),
        "idempotency_key": "idem:begin",
    }
    values.update(overrides)
    return BeginArtifactWrite(**values)


def manager(**kwargs):
    storage = InMemoryArtifactStorage(clock=lambda: NOW)
    metadata = InMemoryArtifactMetadataRepository(clock=lambda: NOW, quota=QuotaPolicy(32, 64, 64))
    sink = InMemoryArtifactEventSink()
    return ArtifactManagerService(storage, metadata, clock=lambda: NOW, event_sink=sink), storage, metadata, sink


def test_write_promotes_only_after_seal_and_emits_started_then_stored():
    service, storage, metadata, events = manager()
    ctx = context()
    session = service.begin_write(write_request(ctx))

    appended = service.append(
        AppendArtifactChunk("operation:chunk", ctx, session.write_session_id, 0, 11, checksum(b"hello world"), "idem:chunk"),
        BytesIO(b"hello world"),
    )
    reference = service.finalize(
        FinalizeArtifactWrite("operation:finalize", ctx, session.write_session_id, 11, checksum(b"hello world"), "idem:finalize")
    )

    assert appended.accepted_offset_bytes == 11
    assert reference.artifact_id == session.artifact_id
    assert metadata.get(ctx, session.artifact_id).metadata.state.value == "AVAILABLE"
    assert [event.event_type for event in events.events] == ["ArtifactWriteStarted", "ArtifactStored"]


def test_wrong_checksum_never_creates_reference_and_abort_is_idempotent():
    service, storage, metadata, events = manager()
    ctx = context()
    session = service.begin_write(write_request(ctx, expected_checksum=None))
    service.append(AppendArtifactChunk("operation:chunk", ctx, session.write_session_id, 0, 3, checksum(b"bad"), "idem:chunk"), BytesIO(b"bad"))

    result = service.finalize(FinalizeArtifactWrite("operation:finalize", ctx, session.write_session_id, 3, checksum(b"expected"), "idem:finalize"))
    aborted = service.abort(AbortArtifactWrite("operation:abort", ctx, session.write_session_id, "caller cancelled", "idem:abort"))

    assert isinstance(result, ArtifactError)
    assert result.code is ArtifactErrorCode.CHECKSUM_MISMATCH
    assert aborted.effect_state is EffectState.APPLIED
    assert metadata.get(ctx, session.artifact_id).metadata.state.value == "DELETED"


def test_quota_is_reserved_before_first_chunk():
    service, storage, metadata, events = manager()
    ctx = context()
    first = service.begin_write(write_request(ctx, expected_size_bytes=32, expected_checksum=None))
    second = service.begin_write(write_request(ctx, operation_id="operation:begin-2", expected_size_bytes=1, idempotency_key="idem:begin-2"))

    assert first.artifact_id
    assert isinstance(second, ArtifactError)
    assert second.code is ArtifactErrorCode.QUOTA_EXCEEDED


def test_begin_finalize_and_abort_retries_reuse_the_original_effect():
    service, storage, metadata, events = manager()
    ctx = context()
    request = write_request(ctx)
    first_session = service.begin_write(request)
    repeated_session = service.begin_write(request)
    service.append(AppendArtifactChunk("operation:chunk", ctx, first_session.write_session_id, 0, 11, checksum(b"hello world"), "idem:chunk"), BytesIO(b"hello world"))
    finalize = FinalizeArtifactWrite("operation:finalize", ctx, first_session.write_session_id, 11, checksum(b"hello world"), "idem:finalize")
    first_reference = service.finalize(finalize)
    repeated_reference = service.finalize(finalize)

    assert repeated_session.artifact_id == first_session.artifact_id
    assert repeated_reference == first_reference
    assert [event.event_type for event in events.events].count("ArtifactStored") == 1


def test_begin_rejects_provenance_from_another_execution_and_expired_abort_is_allowed():
    service, storage, metadata, events = manager()
    ctx = context()
    foreign = context(execution_id="execution:2")
    bad = service.begin_write(write_request(ctx, provenance=ArtifactProvenance(ArtifactProvenanceKind.AGENT_RESULT, (), foreign)))
    assert isinstance(bad, ArtifactError)
    assert bad.code is ArtifactErrorCode.UNAUTHORIZED

    session = service.begin_write(write_request(ctx, expected_size_bytes=1, expected_checksum=None, idempotency_key="expired-abort"))
    service._writes[session.write_session_id.value].storage_handle = service._writes[session.write_session_id.value].storage_handle.__class__(session.write_session_id, "storage-object:expired", 0, NOW - timedelta(seconds=1))
    aborted = service.abort(AbortArtifactWrite("abort", ctx, session.write_session_id, "expired cleanup", "expired-abort-idem"))
    assert not isinstance(aborted, ArtifactError)
