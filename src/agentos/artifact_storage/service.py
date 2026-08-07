from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Callable

from agentos.events.models import DataClassification, EventEnvelope, classification_allows

from .metadata import ArtifactMetadataRecord, InMemoryArtifactMetadataRepository, MetadataMutationReceipt
from .models import (
    AccessPurpose,
    ArtifactCategory,
    ArtifactError,
    ArtifactErrorCode,
    ArtifactGrant,
    ArtifactMetadata,
    ArtifactNamespace,
    ArtifactOperationContext,
    ArtifactReference,
    ArtifactState,
    ContentChecksum,
    EffectState,
    Retryability,
    OpaqueArtifactRef,
)
from .ports import (
    AbortArtifactWrite,
    AppendArtifactChunk,
    ApplyArtifactRetention,
    ArtifactDeletionReceipt,
    ArtifactManager,
    ArtifactReadSession,
    ArtifactRetentionReceipt,
    ArtifactVerifyReceipt,
    ArtifactWriteSession,
    BeginArtifactWrite,
    ByteSink,
    ByteSource,
    DeleteArtifact,
    FinalizeArtifactWrite,
    InspectArtifact,
    OpenArtifactRead,
    ReadArtifactRange,
    StorageAbortReceipt,
    StorageAbortStaging,
    StorageBeginStaging,
    StorageDeleteObject,
    StorageOpenRead,
    StorageReadRange,
    StorageSealObject,
    StorageStagingHandle,
    StorageVerifyObject,
    StorageWriteChunk,
    VerifyArtifact,
)
from .security import derive_namespace, sanitize_logical_name, sanitize_public_reason


class InMemoryArtifactEventSink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    def append(self, event: EventEnvelope) -> None:
        self.events.append(event)


@dataclass
class _WriteBinding:
    request: BeginArtifactWrite
    namespace: ArtifactNamespace
    storage_handle: StorageStagingHandle
    artifact_id: str
    maximum_size_bytes: int


@dataclass
class _ReadBinding:
    request: OpenArtifactRead
    session: ArtifactReadSession
    namespace: ArtifactNamespace
    artifact_id: str
    grant: ArtifactGrant


class ArtifactManagerService:
    def __init__(
        self,
        storage,
        metadata: InMemoryArtifactMetadataRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        event_sink: InMemoryArtifactEventSink | None = None,
    ) -> None:
        self.storage = storage
        self.metadata = metadata
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.event_sink = event_sink or InMemoryArtifactEventSink()
        self._counter = 0
        self._writes: dict[str, _WriteBinding] = {}
        self._reads: dict[str, _ReadBinding] = {}
        self._grants: dict[str, ArtifactGrant] = {}
        self._sequences: dict[str, int] = {}
        self._begin_idempotency: dict[tuple[tuple[str, ...], str], tuple[str, ArtifactWriteSession]] = {}
        self._finalize_idempotency: dict[tuple[tuple[str, ...], str], tuple[str, ArtifactReference]] = {}
        self._abort_idempotency: dict[tuple[tuple[str, ...], str], tuple[str, object]] = {}

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value

    def _next(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}:{self._counter}"

    @staticmethod
    def _error(code: ArtifactErrorCode, *, effect_state=EffectState.NOT_APPLIED, retryability=Retryability.NON_RETRYABLE, message="artifact operation failed") -> ArtifactError:
        return ArtifactError(code, retryability, effect_state, message)

    @staticmethod
    def _owner_matches(ctx: ArtifactOperationContext, metadata: ArtifactMetadata) -> bool:
        return metadata.provenance.created_by.user_id == ctx.user_id and metadata.provenance.created_by.workspace_id == ctx.workspace_id and metadata.provenance.created_by.agent_id == ctx.agent_id and metadata.provenance.created_by.execution_id == ctx.execution_id

    def namespace_for(self, context: ArtifactOperationContext, category: ArtifactCategory) -> ArtifactNamespace:
        return derive_namespace(context.user_id, context.workspace_id, ArtifactCategory(category).value)

    def _event(self, event_type: str, context: ArtifactOperationContext, metadata: ArtifactMetadata, *, reason: str | None = None) -> None:
        if getattr(self.metadata, "events_are_transactional", False):
            return
        sequence = self._sequences.get(context.execution_id, 0) + 1
        self._sequences[context.execution_id] = sequence
        payload = {
            "artifact_id": metadata.artifact_id,
            "category": metadata.category.value,
            "version": metadata.version,
            "size_bytes": metadata.size_bytes,
            "checksum_algorithm": metadata.checksum.algorithm.value,
        }
        if reason:
            payload["reason_code"] = sanitize_public_reason(reason)
        self.event_sink.append(EventEnvelope(
            event_id=self._next("event"),
            event_type=event_type,
            event_version=1,
            occurred_at=self._now(),
            source="artifact-storage",
            correlation_id=context.correlation_id,
            causation_id=None,
            sequence=sequence,
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            execution_id=context.execution_id,
            agent_id=context.agent_id,
            classification=metadata.classification,
            payload=payload,
        ))

    def begin_write(self, request: BeginArtifactWrite) -> ArtifactWriteSession | ArtifactError:
        try:
            category = ArtifactCategory(request.category)
            classification = DataClassification(request.classification)
            logical_name = sanitize_logical_name(request.logical_name)
        except (ValueError, TypeError):
            return self._error(ArtifactErrorCode.INVALID_REQUEST)
        fingerprint = hashlib.sha256(json.dumps((request.operation_id, request.category.value, request.logical_name, request.declared_media_type, request.expected_size_bytes, repr(request.expected_checksum), request.classification.value, request.retention_policy_ref, request.idempotency_key), default=str).encode()).hexdigest()
        begin_key = (request.context.scope_key(), request.idempotency_key)
        prior_begin = self._begin_idempotency.get(begin_key)
        if prior_begin is not None:
            if prior_begin[0] != fingerprint:
                return self._error(ArtifactErrorCode.IDEMPOTENCY_CONFLICT)
            return prior_begin[1]
        namespace = self.namespace_for(request.context, category)
        capabilities = self.storage.capabilities(request.context, namespace)
        maximum_size = request.expected_size_bytes if request.expected_size_bytes is not None else min(capabilities.maximum_object_bytes, self.metadata.quota.max_staging_bytes)
        if maximum_size < 0 or maximum_size > capabilities.maximum_object_bytes:
            return self._error(ArtifactErrorCode.SIZE_LIMIT_EXCEEDED)
        if request.expected_size_bytes is not None and request.expected_size_bytes > capabilities.maximum_object_bytes:
            return self._error(ArtifactErrorCode.SIZE_LIMIT_EXCEEDED)
        artifact_id = self._next("artifact")
        expiry = self._now() + timedelta(hours=1)
        storage_result = self.storage.begin_staging(StorageBeginStaging(
            operation_id=request.operation_id,
            context=request.context,
            namespace=namespace,
            expected_size_bytes=request.expected_size_bytes,
            checksum_algorithm=request.expected_checksum.algorithm if request.expected_checksum else capabilities.checksum_algorithms[0],
            maximum_size_bytes=maximum_size,
            expires_at=expiry,
            idempotency_key=request.idempotency_key,
        ))
        if isinstance(storage_result, ArtifactError):
            return storage_result
        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            namespace=namespace,
            logical_name=logical_name,
            category=category,
            media_type=request.declared_media_type or "application/octet-stream",
            declared_media_type=request.declared_media_type,
            size_bytes=0,
            checksum=request.expected_checksum or ContentChecksum(capabilities.checksum_algorithms[0], "0" * 64),
            classification=classification,
            provenance=request.provenance,
            retention_policy_ref=request.retention_policy_ref,
            state=ArtifactState.STAGING,
            version=1,
            created_at=self._now(),
            available_at=None,
            expires_at=expiry,
        )
        metadata_result = self.metadata.create_staging(
            ArtifactMetadataRecord(metadata, storage_result.storage_object_id, request.context),
            reservation_bytes=maximum_size,
            idempotency_key=request.idempotency_key,
        )
        if isinstance(metadata_result, ArtifactError):
            self.storage.abort_staging(StorageAbortStaging(request.operation_id, request.context, namespace, storage_result.staging_ref, "metadata rejected", self._next("abort-idem")))
            return metadata_result
        session = ArtifactWriteSession(storage_result.staging_ref, artifact_id, 0, maximum_size, expiry, ArtifactState.STAGING)
        self._writes[storage_result.staging_ref.value] = _WriteBinding(request, namespace, storage_result, artifact_id, maximum_size)
        self._begin_idempotency[begin_key] = (fingerprint, session)
        self._event("ArtifactWriteStarted", request.context, metadata)
        return session

    def _write_binding(self, request: AppendArtifactChunk | FinalizeArtifactWrite | AbortArtifactWrite) -> _WriteBinding | ArtifactError:
        binding = self._writes.get(request.write_session_id.value)
        if binding is None:
            return self._error(ArtifactErrorCode.INVALID_HANDLE)
        if binding.request.context.scope_key() != request.context.scope_key():
            return self._error(ArtifactErrorCode.OWNERSHIP_MISMATCH)
        if self._now() >= binding.storage_handle.expires_at:
            return self._error(ArtifactErrorCode.HANDLE_EXPIRED)
        return binding

    def append(self, request: AppendArtifactChunk, source: ByteSource):
        binding = self._write_binding(request)
        if isinstance(binding, ArtifactError):
            return binding
        result = self.storage.write_chunk(StorageWriteChunk(request.operation_id, request.context, binding.namespace, request.write_session_id, request.offset_bytes, request.length_bytes, request.chunk_checksum, request.idempotency_key), source)
        if isinstance(result, ArtifactError):
            return result
        return result

    def finalize(self, request: FinalizeArtifactWrite) -> ArtifactReference | ArtifactError:
        finalize_key = (request.context.scope_key(), request.idempotency_key)
        finalize_fingerprint = hashlib.sha256(json.dumps((request.write_session_id.value, request.expected_total_size_bytes, repr(request.expected_checksum)), default=str).encode()).hexdigest()
        prior_finalize = self._finalize_idempotency.get(finalize_key)
        if prior_finalize is not None:
            if prior_finalize[0] != finalize_fingerprint:
                return self._error(ArtifactErrorCode.IDEMPOTENCY_CONFLICT)
            return prior_finalize[1]
        binding = self._write_binding(request)
        if isinstance(binding, ArtifactError):
            return binding
        result = self.storage.seal(StorageSealObject(request.operation_id, request.context, binding.namespace, request.write_session_id, request.expected_total_size_bytes, request.expected_checksum, True, request.idempotency_key))
        if isinstance(result, ArtifactError):
            return result
        bound = self.metadata.bind_storage_object(request.context, binding.artifact_id, str(result.object_ref))
        if isinstance(bound, ArtifactError):
            return bound
        published = self.metadata.publish_available(request.context, binding.artifact_id, size_bytes=result.size_bytes, checksum=result.computed_checksum, idempotency_key=request.idempotency_key)
        if isinstance(published, ArtifactError):
            self.metadata.transition(request.context, binding.artifact_id, ArtifactState.QUARANTINED, reason="metadata publish failed", idempotency_key=self._next("quarantine-idem"))
            return published
        metadata = published.metadata
        grant = ArtifactGrant(
            grant_id=self._next("grant"), artifact_id=metadata.artifact_id, user_id=request.context.user_id,
            workspace_id=request.context.workspace_id, agent_id=request.context.agent_id, execution_id=request.context.execution_id,
            purpose=AccessPurpose("artifact.read"), classification_ceiling=metadata.classification, version=metadata.version,
            expires_at=metadata.expires_at, revoked_at=None,
        )
        self._grants[grant.grant_id] = grant
        self._event("ArtifactStored", request.context, metadata)
        reference = ArtifactReference(metadata.artifact_id, metadata.version, request.context.user_id, request.context.workspace_id, metadata.category, metadata.size_bytes, metadata.checksum, metadata.classification, grant.grant_id, grant.purpose, metadata.expires_at)
        self._finalize_idempotency[finalize_key] = (finalize_fingerprint, reference)
        return reference

    def abort(self, request: AbortArtifactWrite):
        abort_key = (request.context.scope_key(), request.idempotency_key)
        abort_fingerprint = hashlib.sha256(json.dumps((request.write_session_id.value, request.reason), default=str).encode()).hexdigest()
        prior_abort = self._abort_idempotency.get(abort_key)
        if prior_abort is not None:
            if prior_abort[0] != abort_fingerprint:
                return self._error(ArtifactErrorCode.IDEMPOTENCY_CONFLICT)
            return prior_abort[1]
        binding = self._write_binding(request)
        if isinstance(binding, ArtifactError):
            return binding
        storage_result = self.storage.abort_staging(StorageAbortStaging(request.operation_id, request.context, binding.namespace, request.write_session_id, sanitize_public_reason(request.reason), request.idempotency_key))
        if isinstance(storage_result, ArtifactError):
            return storage_result
        metadata_result = self.metadata.abort_staging(request.context, binding.artifact_id, idempotency_key=request.idempotency_key)
        if isinstance(metadata_result, ArtifactError):
            return metadata_result
        self._writes.pop(request.write_session_id.value, None)
        self._abort_idempotency[abort_key] = (abort_fingerprint, metadata_result)
        return metadata_result

    def _check_reference(self, context: ArtifactOperationContext, reference: ArtifactReference, *, purpose: str, allow_non_available: bool = False, require_grant: bool = True) -> tuple[ArtifactMetadataRecord, ArtifactGrant] | ArtifactError:
        record = self.metadata.get(context, reference.artifact_id)
        if record is None:
            return self._error(ArtifactErrorCode.NOT_FOUND)
        metadata = record.metadata
        if not self._owner_matches(context, metadata) or reference.user_id != context.user_id or reference.workspace_id != context.workspace_id:
            return self._error(ArtifactErrorCode.UNAUTHORIZED)
        if metadata.namespace != self.namespace_for(context, metadata.category):
            return self._error(ArtifactErrorCode.NAMESPACE_MISMATCH)
        if (reference.version != metadata.version and not (allow_non_available and reference.version < metadata.version)) or reference.checksum != metadata.checksum:
            return self._error(ArtifactErrorCode.VERSION_CONFLICT)
        grant = self._grants.get(reference.authorization_ref)
        if str(reference.purpose) != purpose:
            return self._error(ArtifactErrorCode.UNAUTHORIZED)
        if require_grant and (grant is None or not grant.allows(context, version=metadata.version, classification=metadata.classification, now=self._now())):
            return self._error(ArtifactErrorCode.UNAUTHORIZED)
        if grant is None:
            grant = ArtifactGrant(
                grant_id=reference.authorization_ref, artifact_id=metadata.artifact_id, user_id=context.user_id,
                workspace_id=context.workspace_id, agent_id=context.agent_id, execution_id=context.execution_id,
                purpose=reference.purpose, classification_ceiling=metadata.classification, version=metadata.version,
                expires_at=metadata.expires_at, revoked_at=None,
            )
        if not allow_non_available and metadata.state is not ArtifactState.AVAILABLE:
            return self._error(ArtifactErrorCode.OBJECT_QUARANTINED if metadata.state is ArtifactState.QUARANTINED else ArtifactErrorCode.NOT_FOUND)
        if reference.expires_at is not None and self._now() >= reference.expires_at:
            return self._error(ArtifactErrorCode.REFERENCE_EXPIRED)
        return record, grant

    def inspect(self, request: InspectArtifact):
        result = self._check_reference(request.context, request.artifact_ref, purpose=request.purpose, allow_non_available=True, require_grant=False)
        if isinstance(result, ArtifactError):
            return result
        return result[0].metadata

    def open_read(self, request: OpenArtifactRead):
        if not classification_allows(DataClassification(request.classification_ceiling), request.artifact_ref.classification):
            return self._error(ArtifactErrorCode.UNAUTHORIZED)
        result = self._check_reference(request.context, request.artifact_ref, purpose=request.purpose)
        if isinstance(result, ArtifactError):
            return result
        record, grant = result
        namespace = record.metadata.namespace
        storage_result = self.storage.open_read(StorageOpenRead(request.operation_id, request.context, namespace, OpaqueArtifactRef(record.storage_object_ref), record.metadata.size_bytes, record.metadata.checksum, request.maximum_bytes, self._now() + timedelta(minutes=5)))
        if isinstance(storage_result, ArtifactError):
            return storage_result
        session = ArtifactReadSession(storage_result.read_ref, record.metadata.artifact_id, record.metadata.version, record.metadata.size_bytes, record.metadata.checksum, request.maximum_bytes, storage_result.expires_at)
        self._reads[storage_result.read_ref.value] = _ReadBinding(request, session, namespace, record.metadata.artifact_id, grant)
        return session

    def read(self, request: ReadArtifactRange, sink: ByteSink):
        binding = self._reads.get(request.read_session_id.value)
        if binding is None:
            return self._error(ArtifactErrorCode.INVALID_HANDLE)
        if self._now() >= binding.session.expires_at:
            return self._error(ArtifactErrorCode.HANDLE_EXPIRED)
        current = self._check_reference(request.context, binding.request.artifact_ref, purpose=binding.request.purpose)
        if isinstance(current, ArtifactError):
            return current
        if current[0].metadata.version != binding.session.version or current[0].metadata.checksum != binding.session.checksum:
            return self._error(ArtifactErrorCode.VERSION_CONFLICT)
        try:
            result = self.storage.read_range(StorageReadRange(request.operation_id, request.context, binding.namespace, request.read_session_id, request.offset_bytes, request.maximum_bytes), sink)
        except (KeyboardInterrupt, TimeoutError):
            return self._error(ArtifactErrorCode.CANCELLED, effect_state=EffectState.APPLIED, retryability=Retryability.AFTER_RECONCILIATION)
        if isinstance(result, ArtifactError):
            return result
        if result.integrity_observation is not None and result.integrity_observation.value != "VERIFIED":
            self.metadata.transition(request.context, binding.artifact_id, ArtifactState.QUARANTINED, reason="read integrity mismatch", idempotency_key=self._next("quarantine-idem"))
            return self._error(ArtifactErrorCode.CHECKSUM_MISMATCH, effect_state=EffectState.APPLIED)
        if result.next_offset_bytes is None:
            metadata = current[0].metadata
            self._event("ArtifactReadFinished", request.context, metadata)
        return result

    def hold(self, context: ArtifactOperationContext, artifact_id: str) -> None:
        self.metadata.hold(context, artifact_id)

    def delete(self, request: DeleteArtifact):
        checked = self._check_reference(request.context, request.artifact_ref, purpose=str(request.artifact_ref.purpose), require_grant=False)
        if isinstance(checked, ArtifactError):
            return checked
        record, _ = checked
        if record.metadata.version != request.expected_version:
            return self._error(ArtifactErrorCode.VERSION_CONFLICT)
        if self.metadata.has_hold(record.metadata.artifact_id):
            return self._error(ArtifactErrorCode.RETENTION_BLOCKED)
        deleting = self.metadata.transition(request.context, record.metadata.artifact_id, ArtifactState.DELETING, reason=request.reason, idempotency_key=request.idempotency_key)
        if isinstance(deleting, ArtifactError):
            return deleting
        recoverable_until = self._now() + request.recovery_window if request.recovery_window > timedelta(0) else None
        storage_result = self.storage.delete(StorageDeleteObject(request.operation_id, request.context, record.metadata.namespace, OpaqueArtifactRef(record.storage_object_ref), record.metadata.checksum, recoverable_until, request.idempotency_key))
        if isinstance(storage_result, ArtifactError):
            if storage_result.effect_state is EffectState.UNKNOWN:
                self._event("ArtifactCleanupFailed", request.context, deleting.metadata, reason="cleanup outcome unknown")
            return storage_result
        deleted = self.metadata.transition(request.context, record.metadata.artifact_id, ArtifactState.DELETED, reason=request.reason, idempotency_key=self._next("deleted-idem"))
        if isinstance(deleted, ArtifactError):
            return deleted
        self._event("ArtifactDeleted", request.context, deleted.metadata, reason=request.reason)
        return ArtifactDeletionReceipt(record.metadata.artifact_id, ArtifactState.DELETED, recoverable_until, EffectState.APPLIED)

    def verify(self, request: VerifyArtifact):
        checked = self._check_reference(request.context, request.artifact_ref, purpose=str(request.artifact_ref.purpose), require_grant=False)
        if isinstance(checked, ArtifactError):
            return checked
        record, _ = checked
        result = self.storage.verify(StorageVerifyObject(request.operation_id, request.context, record.metadata.namespace, OpaqueArtifactRef(record.storage_object_ref), record.metadata.size_bytes, record.metadata.checksum))
        if isinstance(result, ArtifactError):
            return result
        if result.integrity_state.value != "VERIFIED":
            quarantined = self.metadata.transition(request.context, record.metadata.artifact_id, ArtifactState.QUARANTINED, reason="integrity mismatch", idempotency_key=request.idempotency_key)
            if isinstance(quarantined, ArtifactError):
                return quarantined
            self._event("ArtifactQuarantined", request.context, quarantined.metadata, reason="integrity mismatch")
        return ArtifactVerifyReceipt(record.metadata.artifact_id, result.integrity_state, EffectState.APPLIED)

    def apply_retention(self, request: ApplyArtifactRetention):
        records = self.metadata.list_records(request.context, request.namespace, cutoff_at=request.cutoff_at, maximum=request.maximum_artifacts, retention_policy_ref=request.retention_policy_ref)
        transitioned: list[str] = []
        for record in records:
            result = self.metadata.transition(request.context, record.metadata.artifact_id, ArtifactState.EXPIRED, reason="retention cutoff", idempotency_key=f"{request.idempotency_key}:{record.metadata.artifact_id}")
            if not isinstance(result, ArtifactError):
                transitioned.append(record.metadata.artifact_id)
                self._event("ArtifactExpired", request.context, result.metadata, reason="retention cutoff")
        return ArtifactRetentionReceipt(tuple(transitioned), EffectState.APPLIED)


__all__ = ["ArtifactManagerService", "InMemoryArtifactEventSink"]
