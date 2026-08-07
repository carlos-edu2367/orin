from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Callable

from .models import (
    ArtifactError,
    ArtifactErrorCode,
    ArtifactOperationContext,
    ArtifactNamespace,
    ChecksumAlgorithm,
    ContentChecksum,
    EffectState,
    IntegrityState,
    OpaqueArtifactRef,
    OpaqueReadRef,
    OpaqueWriteSessionRef,
    Retryability,
    StorageCapability,
)
from .ports import (
    ArtifactStorageCapabilities,
    ByteSink,
    ByteSource,
    StorageAbortReceipt,
    StorageAbortStaging,
    StorageBeginStaging,
    StorageDeleteObject,
    StorageDeleteReceipt,
    StorageIntegrityReceipt,
    StorageOpenRead,
    StorageReadHandle,
    StorageReadRange,
    StorageReadReceipt,
    StorageSealObject,
    StorageSealedObject,
    StorageStagingHandle,
    StorageVerifyObject,
    StorageWriteChunk,
    StorageWriteReceipt,
)


@dataclass
class _Staging:
    context: ArtifactOperationContext
    namespace: ArtifactNamespace
    storage_object_id: str
    staging_ref: OpaqueWriteSessionRef
    expires_at: datetime
    maximum_size_bytes: int
    checksum_algorithm: ChecksumAlgorithm
    data: bytearray
    chunks: dict[tuple[int, int, str, str], bytes]
    sealed_object_ref: OpaqueArtifactRef | None = None


@dataclass
class _Object:
    context: ArtifactOperationContext
    namespace: ArtifactNamespace
    storage_object_id: str
    object_ref: OpaqueArtifactRef
    data: bytes
    checksum: ContentChecksum
    sealed_at: datetime
    immutable: bool
    state: str = "AVAILABLE"
    recoverable_until: datetime | None = None


class InMemoryArtifactStorage:
    """Deterministic reference adapter; physical location is intentionally absent."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._counter = 0
        self._staging: dict[str, _Staging] = {}
        self._objects: dict[str, _Object] = {}
        self._reads: dict[str, tuple[ArtifactOperationContext, ArtifactNamespace, _Object, datetime, int]] = {}
        self._faults: dict[str, tuple[ArtifactErrorCode, EffectState]] = {}

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value

    def _next(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}:{self._counter}"

    @staticmethod
    def _error(
        code: ArtifactErrorCode,
        *,
        effect_state: EffectState = EffectState.NOT_APPLIED,
        retryability: Retryability = Retryability.NON_RETRYABLE,
    ) -> ArtifactError:
        return ArtifactError(code, retryability, effect_state)

    @staticmethod
    def _same_binding(left: ArtifactOperationContext, right: ArtifactOperationContext) -> bool:
        return (
            left.user_id == right.user_id
            and left.workspace_id == right.workspace_id
            and left.agent_id == right.agent_id
            and left.execution_id == right.execution_id
            and left.correlation_id == right.correlation_id
            and left.actor == right.actor
        )

    def _fault(self, operation: str) -> ArtifactError | None:
        fault = self._faults.pop(operation, None)
        if fault is None:
            return None
        code, effect = fault
        retryability = (
            Retryability.AFTER_RECONCILIATION
            if effect is EffectState.UNKNOWN
            else Retryability.RETRYABLE
        )
        return self._error(code, effect_state=effect, retryability=retryability)

    def fail_next(
        self,
        operation: str,
        code: ArtifactErrorCode = ArtifactErrorCode.IO_UNAVAILABLE,
        effect_state: EffectState = EffectState.UNKNOWN,
    ) -> None:
        self._faults[operation] = (ArtifactErrorCode(code), EffectState(effect_state))

    def capabilities(self, context: ArtifactOperationContext, namespace: ArtifactNamespace) -> ArtifactStorageCapabilities:
        return ArtifactStorageCapabilities(
            supported=(
                StorageCapability.STREAM_WRITE,
                StorageCapability.RESUMABLE_WRITE,
                StorageCapability.RANGE_READ,
                StorageCapability.ATOMIC_SEAL,
                StorageCapability.SERVER_SIDE_CHECKSUM,
                StorageCapability.RECOVERABLE_DELETE,
                StorageCapability.IMMUTABLE_OBJECT,
            ),
            checksum_algorithms=(ChecksumAlgorithm.SHA256,),
            maximum_object_bytes=16 * 1024 * 1024,
            maximum_chunk_bytes=1024 * 1024,
            minimum_recovery_window=timedelta(seconds=0),
            adapter_contract_version=1,
        )

    def begin_staging(self, request: StorageBeginStaging) -> StorageStagingHandle | ArtifactError:
        if request.maximum_size_bytes < 0 or request.maximum_size_bytes > self.capabilities(request.context, request.namespace).maximum_object_bytes:
            return self._error(ArtifactErrorCode.SIZE_LIMIT_EXCEEDED)
        if request.expected_size_bytes is not None and request.expected_size_bytes > request.maximum_size_bytes:
            return self._error(ArtifactErrorCode.SIZE_LIMIT_EXCEEDED)
        if request.checksum_algorithm is not ChecksumAlgorithm.SHA256:
            return self._error(ArtifactErrorCode.INVALID_REQUEST)
        if (fault := self._fault("begin_staging")) is not None:
            return fault
        object_id = self._next("storage-object")
        staging_ref = OpaqueWriteSessionRef(self._next("staging"))
        self._staging[staging_ref.value] = _Staging(
            context=request.context,
            namespace=request.namespace,
            storage_object_id=object_id,
            staging_ref=staging_ref,
            expires_at=request.expires_at,
            maximum_size_bytes=request.maximum_size_bytes,
            checksum_algorithm=request.checksum_algorithm,
            data=bytearray(),
            chunks={},
        )
        return StorageStagingHandle(staging_ref, object_id, 0, request.expires_at)

    def _get_staging(self, request: StorageWriteChunk | StorageSealObject | StorageAbortStaging) -> _Staging | ArtifactError:
        staging = self._staging.get(request.staging_ref.value)
        if staging is None:
            return self._error(ArtifactErrorCode.INVALID_HANDLE)
        if not self._same_binding(staging.context, request.context):
            return self._error(ArtifactErrorCode.OWNERSHIP_MISMATCH)
        if staging.namespace != request.namespace:
            return self._error(ArtifactErrorCode.NAMESPACE_MISMATCH)
        if self._now() >= staging.expires_at:
            return self._error(ArtifactErrorCode.HANDLE_EXPIRED)
        return staging

    @staticmethod
    def _digest(data: bytes) -> ContentChecksum:
        return ContentChecksum(ChecksumAlgorithm.SHA256, hashlib.sha256(data).hexdigest())

    def write_chunk(self, request: StorageWriteChunk, source: ByteSource) -> StorageWriteReceipt | ArtifactError:
        staging = self._get_staging(request)
        if isinstance(staging, ArtifactError):
            return staging
        if request.offset_bytes < 0 or request.length_bytes < 1:
            return self._error(ArtifactErrorCode.INVALID_REQUEST)
        if request.length_bytes > self.capabilities(request.context, request.namespace).maximum_chunk_bytes:
            return self._error(ArtifactErrorCode.SIZE_LIMIT_EXCEEDED)
        if request.offset_bytes + request.length_bytes > staging.maximum_size_bytes:
            return self._error(ArtifactErrorCode.SIZE_LIMIT_EXCEEDED)
        if staging.sealed_object_ref is not None:
            return self._error(ArtifactErrorCode.OBJECT_ALREADY_SEALED)
        data = source.read(request.length_bytes)
        if not isinstance(data, bytes):
            data = bytes(data)
        if len(data) != request.length_bytes:
            return self._error(ArtifactErrorCode.INVALID_REQUEST)
        computed = self._digest(data)
        if request.expected_chunk_checksum is not None and request.expected_chunk_checksum != computed:
            return self._error(ArtifactErrorCode.CHECKSUM_MISMATCH)
        fingerprint = (
            request.offset_bytes,
            request.length_bytes,
            computed.digest,
            request.idempotency_key,
        )
        existing = next((item for key, item in staging.chunks.items() if key[:2] == fingerprint[:2]), None)
        if existing is not None:
            existing_key = next(key for key, item in staging.chunks.items() if item == existing and key[:2] == fingerprint[:2])
            if existing_key != fingerprint:
                return self._error(ArtifactErrorCode.OFFSET_CONFLICT)
            return StorageWriteReceipt(staging.storage_object_id, request.offset_bytes + request.length_bytes, request.length_bytes, computed, EffectState.APPLIED)
        if request.offset_bytes != len(staging.data):
            return self._error(ArtifactErrorCode.OFFSET_CONFLICT)
        if (fault := self._fault("write_chunk")) is not None:
            return fault
        staging.data.extend(data)
        staging.chunks[fingerprint] = data
        return StorageWriteReceipt(staging.storage_object_id, request.offset_bytes + request.length_bytes, request.length_bytes, computed, EffectState.APPLIED)

    def seal(self, request: StorageSealObject) -> StorageSealedObject | ArtifactError:
        staging = self._get_staging(request)
        if isinstance(staging, ArtifactError):
            return staging
        if staging.sealed_object_ref is not None:
            existing = self._objects.get(staging.sealed_object_ref.value)
            if existing is not None:
                return StorageSealedObject(existing.object_ref, existing.storage_object_id, len(existing.data), existing.checksum, existing.immutable, existing.sealed_at, IntegrityState.VERIFIED)
        if len(staging.data) != request.expected_total_size_bytes:
            return self._error(ArtifactErrorCode.CHECKSUM_MISMATCH)
        computed = self._digest(bytes(staging.data))
        if request.expected_checksum is not None and request.expected_checksum != computed:
            return self._error(ArtifactErrorCode.CHECKSUM_MISMATCH)
        if (fault := self._fault("seal")) is not None:
            return fault
        object_ref = OpaqueArtifactRef(self._next("object"))
        sealed_at = self._now()
        obj = _Object(staging.context, staging.namespace, staging.storage_object_id, object_ref, bytes(staging.data), computed, sealed_at, request.require_immutable)
        self._objects[object_ref.value] = obj
        staging.sealed_object_ref = object_ref
        return StorageSealedObject(object_ref, obj.storage_object_id, len(obj.data), obj.checksum, obj.immutable, sealed_at, IntegrityState.VERIFIED)

    def abort_staging(self, request: StorageAbortStaging) -> StorageAbortReceipt | ArtifactError:
        staging = self._get_staging(request)
        if isinstance(staging, ArtifactError):
            return staging
        if staging.sealed_object_ref is not None:
            return self._error(ArtifactErrorCode.OBJECT_ALREADY_SEALED)
        if (fault := self._fault("abort_staging")) is not None:
            return fault
        removed = len(staging.data)
        del self._staging[staging.staging_ref.value]
        return StorageAbortReceipt(staging.storage_object_id, "ABORTED", removed, EffectState.APPLIED, self._now())

    def _get_object(self, request: StorageOpenRead | StorageVerifyObject | StorageDeleteObject) -> _Object | ArtifactError:
        obj = self._objects.get(request.object_ref.value)
        if obj is None:
            return self._error(ArtifactErrorCode.OBJECT_NOT_FOUND)
        if not self._same_binding(obj.context, request.context):
            return self._error(ArtifactErrorCode.OWNERSHIP_MISMATCH)
        if obj.namespace != request.namespace:
            return self._error(ArtifactErrorCode.NAMESPACE_MISMATCH)
        if obj.state != "AVAILABLE":
            return self._error(ArtifactErrorCode.OBJECT_QUARANTINED)
        return obj

    def open_read(self, request: StorageOpenRead) -> StorageReadHandle | ArtifactError:
        obj = self._get_object(request)
        if isinstance(obj, ArtifactError):
            return obj
        if len(obj.data) != request.expected_size_bytes or obj.checksum != request.expected_checksum:
            return self._error(ArtifactErrorCode.CHECKSUM_MISMATCH)
        if request.maximum_bytes < 0:
            return self._error(ArtifactErrorCode.INVALID_REQUEST)
        if self._now() >= request.expires_at:
            return self._error(ArtifactErrorCode.HANDLE_EXPIRED)
        read_ref = OpaqueReadRef(self._next("read"))
        self._reads[read_ref.value] = (request.context, request.namespace, obj, request.expires_at, request.maximum_bytes)
        return StorageReadHandle(read_ref, obj.storage_object_id, len(obj.data), obj.checksum, request.expires_at)

    def read_range(self, request: StorageReadRange, sink: ByteSink) -> StorageReadReceipt | ArtifactError:
        binding = self._reads.get(request.read_ref.value)
        if binding is None:
            return self._error(ArtifactErrorCode.INVALID_HANDLE)
        context, namespace, obj, expires_at, maximum_total = binding
        if not self._same_binding(context, request.context):
            return self._error(ArtifactErrorCode.OWNERSHIP_MISMATCH)
        if namespace != request.namespace:
            return self._error(ArtifactErrorCode.NAMESPACE_MISMATCH)
        if self._now() >= expires_at:
            return self._error(ArtifactErrorCode.HANDLE_EXPIRED)
        if request.offset_bytes < 0 or request.maximum_bytes < 0 or request.maximum_bytes > maximum_total:
            return self._error(ArtifactErrorCode.SIZE_LIMIT_EXCEEDED)
        if request.offset_bytes >= len(obj.data):
            return StorageReadReceipt(obj.storage_object_id, request.offset_bytes, 0, None, len(obj.data), obj.checksum, IntegrityState.VERIFIED, self._now())
        end = min(len(obj.data), request.offset_bytes + request.maximum_bytes)
        chunk = obj.data[request.offset_bytes:end]
        written = sink.write(chunk)
        if written is not None and written != len(chunk):
            return self._error(ArtifactErrorCode.IO_UNAVAILABLE, effect_state=EffectState.APPLIED, retryability=Retryability.AFTER_RECONCILIATION)
        next_offset = end if end < len(obj.data) else None
        return StorageReadReceipt(obj.storage_object_id, request.offset_bytes, len(chunk), next_offset, len(obj.data), obj.checksum, IntegrityState.VERIFIED, self._now())

    def verify(self, request: StorageVerifyObject) -> StorageIntegrityReceipt | ArtifactError:
        obj = self._get_object(request)
        if isinstance(obj, ArtifactError):
            return obj
        if (fault := self._fault("verify")) is not None:
            return fault
        observed = self._digest(obj.data)
        state = IntegrityState.VERIFIED if len(obj.data) == request.expected_size_bytes and observed == request.expected_checksum else IntegrityState.MISMATCH
        return StorageIntegrityReceipt(obj.storage_object_id, len(obj.data), observed, state, self._now())

    def delete(self, request: StorageDeleteObject) -> StorageDeleteReceipt | ArtifactError:
        obj = self._get_object(request)
        if isinstance(obj, ArtifactError):
            if obj.code is ArtifactErrorCode.OBJECT_QUARANTINED:
                return StorageDeleteReceipt(request.object_ref.value, "ALREADY_ABSENT", EffectState.NOT_APPLIED, request.recoverable_until)
            return obj
        if obj.checksum != request.expected_checksum:
            return self._error(ArtifactErrorCode.CHECKSUM_MISMATCH)
        if (fault := self._fault("delete")) is not None:
            return fault
        obj.recoverable_until = request.recoverable_until
        obj.state = "QUARANTINED" if request.recoverable_until and request.recoverable_until > self._now() else "DELETED"
        outcome = "QUARANTINED" if obj.state == "QUARANTINED" else "DELETED"
        return StorageDeleteReceipt(obj.storage_object_id, outcome, EffectState.APPLIED, request.recoverable_until)


__all__ = ["InMemoryArtifactStorage"]
