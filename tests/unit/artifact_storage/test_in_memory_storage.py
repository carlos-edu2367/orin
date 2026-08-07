from datetime import datetime, timedelta, timezone
from io import BytesIO

from agentos.artifact_storage import (
    ArtifactCategory,
    ArtifactError,
    ArtifactErrorCode,
    ArtifactNamespace,
    ArtifactOperationContext,
    ChecksumAlgorithm,
    ContentChecksum,
    EffectState,
    IntegrityState,
    StorageCapability,
)
from agentos.artifact_storage.in_memory import InMemoryArtifactStorage
from agentos.artifact_storage.ports import (
    StorageAbortStaging,
    StorageBeginStaging,
    StorageContext,
    StorageDeleteObject,
    StorageOpenRead,
    StorageReadRange,
    StorageSealObject,
    StorageVerifyObject,
    StorageWriteChunk,
)


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


def checksum(value: bytes) -> ContentChecksum:
    import hashlib

    return ContentChecksum(ChecksumAlgorithm.SHA256, hashlib.sha256(value).hexdigest())


def begin(storage, *, ctx=None, expiry=None):
    ctx = ctx or context()
    namespace = ArtifactNamespace("artifact-ns-test")
    return storage.begin_staging(
        StorageBeginStaging(
            operation_id="operation:begin",
            context=ctx,
            namespace=namespace,
            expected_size_bytes=11,
            checksum_algorithm=ChecksumAlgorithm.SHA256,
            maximum_size_bytes=32,
            expires_at=expiry or NOW + timedelta(minutes=5),
            idempotency_key="idem:begin",
        )
    )


def test_capabilities_are_explicit_and_handles_are_opaque():
    storage = InMemoryArtifactStorage(clock=lambda: NOW)
    capabilities = storage.capabilities(context(), ArtifactNamespace("artifact-ns-test"))

    assert StorageCapability.ATOMIC_SEAL in capabilities.supported
    assert capabilities.maximum_chunk_bytes <= capabilities.maximum_object_bytes
    handle = begin(storage)
    assert "storage-object" not in repr(handle.staging_ref)


def test_write_accepts_matching_repeat_but_rejects_offset_fingerprint_conflict():
    storage = InMemoryArtifactStorage(clock=lambda: NOW)
    handle = begin(storage)
    request = StorageWriteChunk(
        operation_id="operation:chunk",
        context=context(),
        namespace=ArtifactNamespace("artifact-ns-test"),
        staging_ref=handle.staging_ref,
        offset_bytes=0,
        length_bytes=5,
        expected_chunk_checksum=checksum(b"hello"),
        idempotency_key="idem:chunk",
    )

    first = storage.write_chunk(request, BytesIO(b"hello"))
    repeated = storage.write_chunk(request, BytesIO(b"hello"))
    conflict = storage.write_chunk(
        StorageWriteChunk(
            operation_id="operation:chunk-2",
            context=context(),
            namespace=ArtifactNamespace("artifact-ns-test"),
            staging_ref=handle.staging_ref,
            offset_bytes=0,
            length_bytes=5,
            expected_chunk_checksum=checksum(b"world"),
            idempotency_key="idem:other",
        ),
        BytesIO(b"world"),
    )

    assert first.accepted_length_bytes == 5
    assert repeated.accepted_length_bytes == 5
    assert isinstance(conflict, ArtifactError)
    assert conflict.code is ArtifactErrorCode.OFFSET_CONFLICT


def test_seal_calculates_integrity_and_read_respects_range_limit():
    storage = InMemoryArtifactStorage(clock=lambda: NOW)
    handle = begin(storage)
    storage.write_chunk(
        StorageWriteChunk("operation:chunk", context(), ArtifactNamespace("artifact-ns-test"), handle.staging_ref, 0, 11, checksum(b"hello world"), "idem:chunk"),
        BytesIO(b"hello world"),
    )
    sealed = storage.seal(
        StorageSealObject("operation:seal", context(), ArtifactNamespace("artifact-ns-test"), handle.staging_ref, 11, checksum(b"hello world"), True, "idem:seal")
    )
    read_handle = storage.open_read(
        StorageOpenRead("operation:open", context(purpose="artifact.read"), ArtifactNamespace("artifact-ns-test"), sealed.object_ref, 11, checksum(b"hello world"), 5, NOW + timedelta(minutes=1))
    )
    sink = BytesIO()
    receipt = storage.read_range(
        StorageReadRange("operation:read", context(purpose="artifact.read"), ArtifactNamespace("artifact-ns-test"), read_handle.read_ref, 0, 5),
        sink,
    )

    assert sealed.integrity_state is IntegrityState.VERIFIED
    assert sink.getvalue() == b"hello"
    assert receipt.delivered_bytes == 5
    assert receipt.next_offset_bytes == 5


def test_expired_handles_and_wrong_binding_fail_closed():
    storage = InMemoryArtifactStorage(clock=lambda: NOW)
    handle = begin(storage, expiry=NOW - timedelta(seconds=1))
    expired = storage.write_chunk(
        StorageWriteChunk("operation:chunk", context(), ArtifactNamespace("artifact-ns-test"), handle.staging_ref, 0, 1, checksum(b"x"), "idem"),
        BytesIO(b"x"),
    )
    assert isinstance(expired, ArtifactError)
    assert expired.code is ArtifactErrorCode.HANDLE_EXPIRED

    active = begin(storage)
    wrong = storage.write_chunk(
        StorageWriteChunk("operation:chunk", context(user_id="user:2"), ArtifactNamespace("artifact-ns-test"), active.staging_ref, 0, 1, checksum(b"x"), "idem"),
        BytesIO(b"x"),
    )
    assert isinstance(wrong, ArtifactError)
    assert wrong.code is ArtifactErrorCode.OWNERSHIP_MISMATCH


def test_verify_mismatch_and_delete_effect_states_are_explicit():
    storage = InMemoryArtifactStorage(clock=lambda: NOW)
    handle = begin(storage)
    storage.write_chunk(
        StorageWriteChunk("operation:chunk", context(), ArtifactNamespace("artifact-ns-test"), handle.staging_ref, 0, 1, checksum(b"x"), "idem"),
        BytesIO(b"x"),
    )
    sealed = storage.seal(
        StorageSealObject("operation:seal", context(), ArtifactNamespace("artifact-ns-test"), handle.staging_ref, 1, checksum(b"x"), True, "idem:seal")
    )
    mismatch = storage.verify(
        StorageVerifyObject("operation:verify", context(), ArtifactNamespace("artifact-ns-test"), sealed.object_ref, 1, checksum(b"y"))
    )
    deleted = storage.delete(
        StorageDeleteObject("operation:delete", context(), ArtifactNamespace("artifact-ns-test"), sealed.object_ref, checksum(b"x"), NOW + timedelta(minutes=1), "idem:delete")
    )

    assert mismatch.integrity_state is IntegrityState.MISMATCH
    assert deleted.effect_state is EffectState.APPLIED
    assert deleted.outcome == "QUARANTINED"
