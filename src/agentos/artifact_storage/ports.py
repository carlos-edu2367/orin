from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from agentos.events.models import DataClassification

from .models import (
    AccessPurpose, ArtifactCategory, ArtifactError, ArtifactMetadata, ArtifactNamespace, ArtifactOperationContext,
    ArtifactProvenance,
    ArtifactReference, ArtifactState, ChecksumAlgorithm, ContentChecksum, EffectState,
    IntegrityState, OpaqueArtifactRef, OpaqueReadRef, OpaqueWriteSessionRef, Retryability,
    StorageCapability,
)


MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_CHUNK_BYTES = 1024 * 1024


def _bounded_text(value: str, field: str, maximum: int = 255) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} is invalid")


def _bounded_nonnegative(value: int, field: str, maximum: int = MAX_ARTIFACT_BYTES) -> None:
    if not isinstance(value, int) or value < 0 or value > maximum:
        raise ValueError(f"{field} is invalid")


class ByteSource(Protocol):
    def read(self, maximum_bytes: int) -> bytes: ...


class ByteSink(Protocol):
    def write(self, data: bytes) -> int | None: ...


@dataclass(frozen=True, slots=True)
class ArtifactStorageCapabilities:
    supported: tuple[StorageCapability, ...]
    checksum_algorithms: tuple[ChecksumAlgorithm, ...]
    maximum_object_bytes: int
    maximum_chunk_bytes: int
    minimum_recovery_window: timedelta | None
    adapter_contract_version: int = 1


@dataclass(frozen=True, slots=True)
class StorageContext:
    operation_id: str
    context: ArtifactOperationContext
    namespace: ArtifactNamespace

    def __post_init__(self) -> None:
        _bounded_text(self.operation_id, "operation_id")


@dataclass(frozen=True, slots=True)
class StorageBeginStaging(StorageContext):
    expected_size_bytes: int | None
    checksum_algorithm: ChecksumAlgorithm
    maximum_size_bytes: int
    expires_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        StorageContext.__post_init__(self)
        if self.expected_size_bytes is not None:
            _bounded_nonnegative(self.expected_size_bytes, "expected_size_bytes")
        _bounded_nonnegative(self.maximum_size_bytes, "maximum_size_bytes")
        _bounded_text(self.idempotency_key, "idempotency_key", 256)


@dataclass(frozen=True, slots=True)
class StorageStagingHandle:
    staging_ref: OpaqueWriteSessionRef
    storage_object_id: str
    accepted_offset_bytes: int
    expires_at: datetime
    adapter_contract_version: int = 1


@dataclass(frozen=True, slots=True)
class StorageWriteChunk(StorageContext):
    staging_ref: OpaqueWriteSessionRef
    offset_bytes: int
    length_bytes: int
    expected_chunk_checksum: ContentChecksum | None
    idempotency_key: str

    def __post_init__(self) -> None:
        StorageContext.__post_init__(self)
        _bounded_nonnegative(self.offset_bytes, "offset_bytes")
        if not isinstance(self.length_bytes, int) or self.length_bytes < 1 or self.length_bytes > MAX_CHUNK_BYTES:
            raise ValueError("length_bytes is invalid")
        _bounded_text(self.idempotency_key, "idempotency_key", 256)


@dataclass(frozen=True, slots=True)
class StorageWriteReceipt:
    storage_object_id: str
    accepted_offset_bytes: int
    accepted_length_bytes: int
    computed_chunk_checksum: ContentChecksum | None
    effect_state: EffectState


@dataclass(frozen=True, slots=True)
class StorageSealObject(StorageContext):
    staging_ref: OpaqueWriteSessionRef
    expected_total_size_bytes: int
    expected_checksum: ContentChecksum | None
    require_immutable: bool
    idempotency_key: str

    def __post_init__(self) -> None:
        StorageContext.__post_init__(self)
        _bounded_nonnegative(self.expected_total_size_bytes, "expected_total_size_bytes")
        _bounded_text(self.idempotency_key, "idempotency_key", 256)


@dataclass(frozen=True, slots=True)
class StorageSealedObject:
    object_ref: OpaqueArtifactRef
    storage_object_id: str
    size_bytes: int
    computed_checksum: ContentChecksum
    immutable: bool
    sealed_at: datetime
    integrity_state: IntegrityState


@dataclass(frozen=True, slots=True)
class StorageAbortStaging(StorageContext):
    staging_ref: OpaqueWriteSessionRef
    reason: str
    idempotency_key: str

    def __post_init__(self) -> None:
        StorageContext.__post_init__(self)
        _bounded_text(self.reason, "reason", 128)
        _bounded_text(self.idempotency_key, "idempotency_key", 256)


@dataclass(frozen=True, slots=True)
class StorageAbortReceipt:
    storage_object_id: str
    outcome: str
    removed_staging_bytes: int
    effect_state: EffectState
    aborted_at: datetime


@dataclass(frozen=True, slots=True)
class StorageOpenRead(StorageContext):
    object_ref: OpaqueArtifactRef
    expected_size_bytes: int
    expected_checksum: ContentChecksum
    maximum_bytes: int
    expires_at: datetime

    def __post_init__(self) -> None:
        StorageContext.__post_init__(self)
        _bounded_nonnegative(self.expected_size_bytes, "expected_size_bytes")
        _bounded_nonnegative(self.maximum_bytes, "maximum_bytes")


@dataclass(frozen=True, slots=True)
class StorageReadHandle:
    read_ref: OpaqueReadRef
    storage_object_id: str
    size_bytes: int
    checksum: ContentChecksum
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StorageReadRange(StorageContext):
    read_ref: OpaqueReadRef
    offset_bytes: int
    maximum_bytes: int

    def __post_init__(self) -> None:
        StorageContext.__post_init__(self)
        _bounded_nonnegative(self.offset_bytes, "offset_bytes")
        _bounded_nonnegative(self.maximum_bytes, "maximum_bytes")


@dataclass(frozen=True, slots=True)
class StorageReadReceipt:
    storage_object_id: str
    delivered_offset_bytes: int
    delivered_bytes: int
    next_offset_bytes: int | None
    object_size_bytes: int
    expected_checksum: ContentChecksum
    integrity_observation: IntegrityState
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class StorageVerifyObject(StorageContext):
    object_ref: OpaqueArtifactRef
    expected_size_bytes: int
    expected_checksum: ContentChecksum

    def __post_init__(self) -> None:
        StorageContext.__post_init__(self)
        _bounded_nonnegative(self.expected_size_bytes, "expected_size_bytes")


@dataclass(frozen=True, slots=True)
class StorageIntegrityReceipt:
    storage_object_id: str
    observed_size_bytes: int
    observed_checksum: ContentChecksum | None
    integrity_state: IntegrityState
    verified_at: datetime


@dataclass(frozen=True, slots=True)
class StorageDeleteObject(StorageContext):
    object_ref: OpaqueArtifactRef
    expected_checksum: ContentChecksum
    recoverable_until: datetime | None
    idempotency_key: str

    def __post_init__(self) -> None:
        StorageContext.__post_init__(self)
        _bounded_text(self.idempotency_key, "idempotency_key", 256)


@dataclass(frozen=True, slots=True)
class StorageDeleteReceipt:
    storage_object_id: str
    outcome: str
    effect_state: EffectState
    recoverable_until: datetime | None


@dataclass(frozen=True, slots=True)
class BeginArtifactWrite:
    operation_id: str
    context: ArtifactOperationContext
    category: ArtifactCategory
    logical_name: str
    declared_media_type: str | None
    expected_size_bytes: int | None
    expected_checksum: ContentChecksum | None
    classification: DataClassification
    retention_policy_ref: str
    provenance: ArtifactProvenance
    idempotency_key: str

    def __post_init__(self) -> None:
        _bounded_text(self.operation_id, "operation_id")
        _bounded_text(self.logical_name, "logical_name")
        if self.expected_size_bytes is not None:
            _bounded_nonnegative(self.expected_size_bytes, "expected_size_bytes")
        _bounded_text(self.retention_policy_ref, "retention_policy_ref")
        _bounded_text(self.idempotency_key, "idempotency_key", 256)


@dataclass(frozen=True, slots=True)
class ArtifactWriteSession:
    write_session_id: OpaqueWriteSessionRef
    artifact_id: str
    accepted_offset_bytes: int
    maximum_size_bytes: int
    expires_at: datetime
    state: ArtifactState


@dataclass(frozen=True, slots=True)
class AppendArtifactChunk:
    operation_id: str
    context: ArtifactOperationContext
    write_session_id: OpaqueWriteSessionRef
    offset_bytes: int
    length_bytes: int
    chunk_checksum: ContentChecksum | None
    idempotency_key: str

    def __post_init__(self) -> None:
        _bounded_text(self.operation_id, "operation_id")
        _bounded_nonnegative(self.offset_bytes, "offset_bytes")
        if self.length_bytes < 1 or self.length_bytes > MAX_CHUNK_BYTES:
            raise ValueError("length_bytes is invalid")
        _bounded_text(self.idempotency_key, "idempotency_key", 256)


@dataclass(frozen=True, slots=True)
class FinalizeArtifactWrite:
    operation_id: str
    context: ArtifactOperationContext
    write_session_id: OpaqueWriteSessionRef
    expected_total_size_bytes: int
    expected_checksum: ContentChecksum | None
    idempotency_key: str

    def __post_init__(self) -> None:
        _bounded_text(self.operation_id, "operation_id")
        _bounded_nonnegative(self.expected_total_size_bytes, "expected_total_size_bytes")
        _bounded_text(self.idempotency_key, "idempotency_key", 256)


@dataclass(frozen=True, slots=True)
class AbortArtifactWrite:
    operation_id: str
    context: ArtifactOperationContext
    write_session_id: OpaqueWriteSessionRef
    reason: str
    idempotency_key: str

    def __post_init__(self) -> None:
        _bounded_text(self.operation_id, "operation_id")
        _bounded_text(self.reason, "reason", 128)
        _bounded_text(self.idempotency_key, "idempotency_key", 256)


@dataclass(frozen=True, slots=True)
class OpenArtifactRead:
    operation_id: str
    context: ArtifactOperationContext
    artifact_ref: ArtifactReference
    maximum_bytes: int
    purpose: AccessPurpose
    classification_ceiling: DataClassification = DataClassification.RESTRICTED

    def __post_init__(self) -> None:
        _bounded_text(self.operation_id, "operation_id")
        _bounded_nonnegative(self.maximum_bytes, "maximum_bytes")
        _bounded_text(self.purpose, "purpose", 128)


@dataclass(frozen=True, slots=True)
class ArtifactReadSession:
    read_session_id: OpaqueReadRef
    artifact_id: str
    version: int
    size_bytes: int
    checksum: ContentChecksum
    maximum_bytes: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ReadArtifactRange:
    operation_id: str
    context: ArtifactOperationContext
    read_session_id: OpaqueReadRef
    offset_bytes: int
    maximum_bytes: int

    def __post_init__(self) -> None:
        _bounded_text(self.operation_id, "operation_id")
        _bounded_nonnegative(self.offset_bytes, "offset_bytes")
        _bounded_nonnegative(self.maximum_bytes, "maximum_bytes")


@dataclass(frozen=True, slots=True)
class InspectArtifact:
    context: ArtifactOperationContext
    artifact_ref: ArtifactReference
    purpose: AccessPurpose


@dataclass(frozen=True, slots=True)
class DeleteArtifact:
    operation_id: str
    context: ArtifactOperationContext
    artifact_ref: ArtifactReference
    expected_version: int
    reason: str
    recovery_window: timedelta
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ArtifactDeletionReceipt:
    artifact_id: str
    state: ArtifactState
    recoverable_until: datetime | None
    effect_state: EffectState


@dataclass(frozen=True, slots=True)
class ApplyArtifactRetention:
    operation_id: str
    context: ArtifactOperationContext
    namespace: ArtifactNamespace
    retention_policy_ref: str
    cutoff_at: datetime
    maximum_artifacts: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ArtifactRetentionReceipt:
    transitioned_artifact_ids: tuple[str, ...]
    effect_state: EffectState


@dataclass(frozen=True, slots=True)
class ArtifactAbortResult:
    artifact_id: str
    state: ArtifactState
    effect_state: EffectState


@dataclass(frozen=True, slots=True)
class ReconcileArtifactCleanup:
    operation_id: str
    context: ArtifactOperationContext
    artifact_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ArtifactCleanupReceipt:
    artifact_id: str
    state: ArtifactState
    effect_state: EffectState


@dataclass(frozen=True, slots=True)
class VerifyArtifact:
    operation_id: str
    context: ArtifactOperationContext
    artifact_ref: ArtifactReference
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ArtifactVerifyReceipt:
    artifact_id: str
    integrity_state: IntegrityState
    effect_state: EffectState


class ArtifactStorage(Protocol):
    def capabilities(self, context: ArtifactOperationContext, namespace: ArtifactNamespace) -> ArtifactStorageCapabilities: ...
    def begin_staging(self, request: StorageBeginStaging) -> StorageStagingHandle | ArtifactError: ...
    def write_chunk(self, request: StorageWriteChunk, source: ByteSource) -> StorageWriteReceipt | ArtifactError: ...
    def seal(self, request: StorageSealObject) -> StorageSealedObject | ArtifactError: ...
    def abort_staging(self, request: StorageAbortStaging) -> StorageAbortReceipt | ArtifactError: ...
    def open_read(self, request: StorageOpenRead) -> StorageReadHandle | ArtifactError: ...
    def read_range(self, request: StorageReadRange, sink: ByteSink) -> StorageReadReceipt | ArtifactError: ...
    def verify(self, request: StorageVerifyObject) -> StorageIntegrityReceipt | ArtifactError: ...
    def delete(self, request: StorageDeleteObject) -> StorageDeleteReceipt | ArtifactError: ...


class ArtifactMetadataRepository(Protocol):
    def get(self, context: ArtifactOperationContext, artifact_id: str) -> ArtifactMetadata | None: ...
    def create_staging(self, record: object, *, reservation_bytes: int, idempotency_key: str) -> object: ...
    def publish_available(self, context: ArtifactOperationContext, artifact_id: str, *, size_bytes: int, checksum: ContentChecksum, idempotency_key: str) -> object: ...
    def abort_staging(self, context: ArtifactOperationContext, artifact_id: str, *, idempotency_key: str) -> object: ...
    def transition(self, context: ArtifactOperationContext, artifact_id: str, state: ArtifactState, *, reason: str, idempotency_key: str) -> object: ...


class ArtifactManager(Protocol):
    def begin_write(self, request: BeginArtifactWrite) -> ArtifactWriteSession | ArtifactError: ...
    def append(self, request: AppendArtifactChunk, source: ByteSource) -> StorageWriteReceipt | ArtifactError: ...
    def finalize(self, request: FinalizeArtifactWrite) -> ArtifactReference | ArtifactError: ...
    def abort(self, request: AbortArtifactWrite) -> ArtifactAbortResult | ArtifactError: ...
    def open_read(self, request: OpenArtifactRead) -> ArtifactReadSession | ArtifactError: ...
    def read(self, request: ReadArtifactRange, sink: ByteSink) -> StorageReadReceipt | ArtifactError: ...
    def inspect(self, request: InspectArtifact) -> ArtifactMetadata | ArtifactError: ...
    def verify(self, request: VerifyArtifact) -> ArtifactVerifyReceipt | ArtifactError: ...
    def delete(self, request: DeleteArtifact) -> ArtifactDeletionReceipt | ArtifactError: ...
    def apply_retention(self, request: ApplyArtifactRetention) -> ArtifactRetentionReceipt | ArtifactError: ...
    def reconcile_cleanup(self, request: ReconcileArtifactCleanup) -> ArtifactCleanupReceipt | ArtifactError: ...


__all__ = [name for name in globals() if not name.startswith("_")]
