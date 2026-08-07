from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Callable

from agentos.events.models import DataClassification
from agentos.persistence import (
    AuditChange,
    AuthorizedRead,
    ExpectedVersion,
    InMemoryTransactionalPersistence,
    PersistenceOperationContext,
    RecordChange,
    RecordReference,
    TransactionCommitted,
    TransactionOptions,
    TransactionRequest,
)

from .models import (
    AccessPurpose,
    ArtifactCategory,
    ArtifactError,
    ArtifactErrorCode,
    ArtifactMetadata,
    ArtifactNamespace,
    ArtifactOperationContext,
    ArtifactProvenance,
    ArtifactProvenanceKind,
    ArtifactState,
    ChecksumAlgorithm,
    ContentChecksum,
    EffectState,
    Retryability,
)


@dataclass(frozen=True, slots=True)
class QuotaPolicy:
    max_staging_bytes: int
    max_available_bytes: int
    max_recovery_bytes: int = 0
    max_artifacts: int = 10000

    def __post_init__(self) -> None:
        if min(self.max_staging_bytes, self.max_available_bytes, self.max_recovery_bytes, self.max_artifacts) < 0:
            raise ValueError("quota limits cannot be negative")


@dataclass(frozen=True, slots=True)
class QuotaUsage:
    staging_bytes: int = 0
    available_bytes: int = 0
    recovery_bytes: int = 0
    artifact_count: int = 0


@dataclass(frozen=True, slots=True)
class ArtifactMetadataRecord:
    metadata: ArtifactMetadata
    storage_object_ref: str
    context: ArtifactOperationContext

    def __post_init__(self) -> None:
        if not self.storage_object_ref or len(self.storage_object_ref) > 255:
            raise ValueError("storage_object_ref is invalid")

    def __repr__(self) -> str:
        return (
            "ArtifactMetadataRecord("
            f"artifact_id={self.metadata.artifact_id!r}, state={self.metadata.state.value!r}, "
            f"version={self.metadata.version}, storage_object_ref=<opaque>)"
        )


@dataclass(frozen=True, slots=True)
class MetadataMutationReceipt:
    record: ArtifactMetadataRecord
    effect_state: EffectState
    already_applied: bool = False

    @property
    def metadata(self) -> ArtifactMetadata:
        return self.record.metadata


class InMemoryArtifactMetadataRepository:
    def __init__(self, *, clock: Callable[[], datetime] | None = None, quota: QuotaPolicy | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.quota = quota or QuotaPolicy(64 * 1024 * 1024, 256 * 1024 * 1024, 256 * 1024 * 1024)
        self._records: dict[str, ArtifactMetadataRecord] = {}
        self._idempotency: dict[tuple[tuple[str, ...], str], tuple[str, MetadataMutationReceipt]] = {}
        self._reservations: dict[str, int] = {}
        self._holds: set[str] = set()
        self._grants: dict[str, object] = {}
        self.events: list[object] = []

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value

    @staticmethod
    def _error(code: ArtifactErrorCode, *, effect_state=EffectState.NOT_APPLIED) -> ArtifactError:
        return ArtifactError(code, Retryability.NON_RETRYABLE, effect_state)

    def usage(self, context: ArtifactOperationContext) -> QuotaUsage:
        records = [record for record in self._records.values() if self._same_owner(record.context, context)]
        staging = sum(self._reservations.get(record.metadata.artifact_id, 0) for record in records if record.metadata.state is ArtifactState.STAGING)
        available = sum(record.metadata.size_bytes for record in records if record.metadata.state is ArtifactState.AVAILABLE)
        recovery = sum(record.metadata.size_bytes for record in records if record.metadata.state is ArtifactState.DELETING)
        return QuotaUsage(staging, available, recovery, len(records))

    def _remember(self, context: ArtifactOperationContext, idempotency_key: str, fingerprint: str, receipt: MetadataMutationReceipt) -> MetadataMutationReceipt | ArtifactError:
        key = (context.scope_key(), idempotency_key)
        prior = self._idempotency.get(key)
        if prior is not None:
            if prior[0] != fingerprint:
                return self._error(ArtifactErrorCode.VERSION_CONFLICT)
            return replace(prior[1], already_applied=True)
        self._idempotency[key] = (fingerprint, receipt)
        return receipt

    def _fingerprint(self, *values: object) -> str:
        return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()

    def create_staging(self, record: ArtifactMetadataRecord, *, reservation_bytes: int, idempotency_key: str) -> MetadataMutationReceipt | ArtifactError:
        ctx = record.context
        fingerprint = self._fingerprint("create", record.metadata.artifact_id, reservation_bytes, record.metadata)
        prior = self._idempotency.get((ctx.scope_key(), idempotency_key))
        if prior is not None:
            return self._remember(ctx, idempotency_key, fingerprint, prior[1])
        if record.metadata.artifact_id in self._records:
            return self._error(ArtifactErrorCode.VERSION_CONFLICT)
        usage = self.usage(ctx)
        if usage.staging_bytes + reservation_bytes > self.quota.max_staging_bytes or usage.artifact_count >= self.quota.max_artifacts:
            return self._error(ArtifactErrorCode.QUOTA_EXCEEDED)
        if reservation_bytes < 0:
            return self._error(ArtifactErrorCode.INVALID_REQUEST)
        self._records[record.metadata.artifact_id] = record
        self._reservations[record.metadata.artifact_id] = reservation_bytes
        receipt = MetadataMutationReceipt(record, EffectState.APPLIED)
        remembered = self._remember(ctx, idempotency_key, fingerprint, receipt)
        return remembered

    def get(self, context: ArtifactOperationContext, artifact_id: str) -> ArtifactMetadataRecord | None:
        record = self._records.get(artifact_id)
        if record is None or not self._same_owner(record.context, context):
            return None
        return record

    def bind_storage_object(self, context: ArtifactOperationContext, artifact_id: str, storage_object_ref: str) -> ArtifactMetadataRecord | ArtifactError:
        record = self.get(context, artifact_id)
        if record is None:
            return self._error(ArtifactErrorCode.NOT_FOUND)
        updated = replace(record, storage_object_ref=storage_object_ref)
        self._records[artifact_id] = updated
        return updated

    @staticmethod
    def _same_owner(left: ArtifactOperationContext, right: ArtifactOperationContext) -> bool:
        return (
            left.user_id == right.user_id
            and left.workspace_id == right.workspace_id
            and left.agent_id == right.agent_id
            and left.execution_id == right.execution_id
            and left.correlation_id == right.correlation_id
            and left.actor == right.actor
        )

    def list_records(self, context: ArtifactOperationContext, namespace: ArtifactNamespace, *, cutoff_at: datetime, maximum: int, retention_policy_ref: str) -> tuple[ArtifactMetadataRecord, ...]:
        if maximum < 1:
            return ()
        records = [
            record for record in self._records.values()
            if self._same_owner(record.context, context)
            and record.metadata.namespace == namespace
            and record.metadata.retention_policy_ref == retention_policy_ref
            and record.metadata.state is ArtifactState.AVAILABLE
            and record.metadata.created_at <= cutoff_at
        ]
        return tuple(sorted(records, key=lambda item: item.metadata.artifact_id)[:maximum])

    def publish_available(self, context: ArtifactOperationContext, artifact_id: str, *, size_bytes: int, checksum: ContentChecksum, idempotency_key: str) -> MetadataMutationReceipt | ArtifactError:
        record = self.get(context, artifact_id)
        if record is None:
            return self._error(ArtifactErrorCode.NOT_FOUND)
        fingerprint = self._fingerprint("publish", artifact_id, size_bytes, checksum.digest)
        prior = self._idempotency.get((context.scope_key(), idempotency_key))
        if prior is not None:
            return self._remember(context, idempotency_key, fingerprint, prior[1])
        if record.metadata.state is not ArtifactState.STAGING:
            return self._error(ArtifactErrorCode.VERSION_CONFLICT)
        usage = self.usage(context)
        if usage.available_bytes + size_bytes > self.quota.max_available_bytes:
            return self._error(ArtifactErrorCode.QUOTA_EXCEEDED)
        if size_bytes < 0:
            return self._error(ArtifactErrorCode.INVALID_REQUEST)
        updated = replace(record.metadata, state=ArtifactState.AVAILABLE, size_bytes=size_bytes, checksum=checksum, version=record.metadata.version + 1, available_at=self._now())
        updated_record = replace(record, metadata=updated)
        self._records[artifact_id] = updated_record
        self._reservations.pop(artifact_id, None)
        receipt = MetadataMutationReceipt(updated_record, EffectState.APPLIED)
        return self._remember(context, idempotency_key, fingerprint, receipt)

    def abort_staging(self, context: ArtifactOperationContext, artifact_id: str, *, idempotency_key: str) -> MetadataMutationReceipt | ArtifactError:
        record = self.get(context, artifact_id)
        if record is None:
            return self._error(ArtifactErrorCode.NOT_FOUND)
        fingerprint = self._fingerprint("abort", artifact_id)
        prior = self._idempotency.get((context.scope_key(), idempotency_key))
        if prior is not None:
            return self._remember(context, idempotency_key, fingerprint, prior[1])
        if record.metadata.state is not ArtifactState.STAGING:
            return self._error(ArtifactErrorCode.VERSION_CONFLICT)
        updated_record = replace(record, metadata=replace(record.metadata, state=ArtifactState.DELETED, version=record.metadata.version + 1))
        self._records[artifact_id] = updated_record
        self._reservations.pop(artifact_id, None)
        receipt = MetadataMutationReceipt(updated_record, EffectState.APPLIED)
        return self._remember(context, idempotency_key, fingerprint, receipt)

    def transition(self, context: ArtifactOperationContext, artifact_id: str, state: ArtifactState, *, reason: str, idempotency_key: str) -> MetadataMutationReceipt | ArtifactError:
        record = self.get(context, artifact_id)
        if record is None:
            return self._error(ArtifactErrorCode.NOT_FOUND)
        fingerprint = self._fingerprint("transition", artifact_id, state.value, reason)
        prior = self._idempotency.get((context.scope_key(), idempotency_key))
        if prior is not None:
            return self._remember(context, idempotency_key, fingerprint, prior[1])
        state = ArtifactState(state)
        allowed = {
            ArtifactState.STAGING: {ArtifactState.QUARANTINED, ArtifactState.DELETED},
            ArtifactState.AVAILABLE: {ArtifactState.QUARANTINED, ArtifactState.EXPIRED, ArtifactState.DELETING},
            ArtifactState.QUARANTINED: {ArtifactState.DELETING, ArtifactState.DELETED},
            ArtifactState.EXPIRED: {ArtifactState.DELETING, ArtifactState.DELETED},
            ArtifactState.DELETING: {ArtifactState.DELETED},
            ArtifactState.DELETED: set(),
        }
        if state not in allowed[record.metadata.state]:
            return self._error(ArtifactErrorCode.VERSION_CONFLICT)
        updated_record = replace(record, metadata=replace(record.metadata, state=state, version=record.metadata.version + 1))
        self._records[artifact_id] = updated_record
        receipt = MetadataMutationReceipt(updated_record, EffectState.APPLIED)
        return self._remember(context, idempotency_key, fingerprint, receipt)

    def hold(self, context: ArtifactOperationContext, artifact_id: str) -> None:
        if self.get(context, artifact_id) is not None:
            self._holds.add(artifact_id)

    def has_hold(self, artifact_id: str) -> bool:
        return artifact_id in self._holds


__all__ = ["ArtifactMetadataRecord", "InMemoryArtifactMetadataRepository", "MetadataMutationReceipt", "QuotaPolicy", "QuotaUsage"]
