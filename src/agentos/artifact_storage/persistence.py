from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from typing import Callable

from agentos.events.models import DataClassification
from agentos.persistence import (
    AuditChange,
    AuthorizedRead,
    ExpectedVersion,
    PersistenceOperationContext,
    RecordChange,
    RecordReference,
    TransactionCommitted,
    TransactionOptions,
    TransactionRequest,
    TransactionalPersistence,
)

from .metadata import ArtifactMetadataRecord, InMemoryArtifactMetadataRepository, MetadataMutationReceipt, QuotaPolicy
from .models import ArtifactCategory, ArtifactError, ArtifactErrorCode, ArtifactNamespace, ArtifactOperationContext, ArtifactProvenance, ArtifactProvenanceKind, ArtifactState, AccessPurpose, ChecksumAlgorithm, ContentChecksum, DataClassification as ArtifactClassification, EffectState, Retryability


class TransactionalArtifactMetadataRepository(InMemoryArtifactMetadataRepository):
    """Metadata adapter that records every mutation through RFC 601's canonical port."""

    def __init__(self, persistence: TransactionalPersistence, *, clock: Callable[[], datetime], quota: QuotaPolicy) -> None:
        super().__init__(clock=clock, quota=quota)
        self.persistence = persistence

    @staticmethod
    def new_record(context: ArtifactOperationContext, *, artifact_id: str, namespace: ArtifactNamespace, category: ArtifactCategory, logical_name: str, classification: DataClassification, storage_object_ref: str) -> ArtifactMetadataRecord:
        now = datetime.now(timezone.utc)
        metadata = __import__("agentos.artifact_storage.models", fromlist=["ArtifactMetadata"]).ArtifactMetadata(
            artifact_id=artifact_id,
            namespace=namespace,
            logical_name=logical_name,
            category=category,
            media_type="application/octet-stream",
            declared_media_type=None,
            size_bytes=0,
            checksum=ContentChecksum(ChecksumAlgorithm.SHA256, "0" * 64),
            classification=ArtifactClassification(classification),
            provenance=ArtifactProvenance(ArtifactProvenanceKind.AGENT_RESULT, (context.execution_id,), context),
            retention_policy_ref="retention:default",
            state=ArtifactState.STAGING,
            version=1,
            created_at=now,
            available_at=None,
            expires_at=None,
        )
        return ArtifactMetadataRecord(metadata, storage_object_ref, context)

    @staticmethod
    def _data(record: ArtifactMetadataRecord) -> dict[str, object]:
        metadata = record.metadata
        return {
            "artifact_id": metadata.artifact_id,
            "namespace": str(metadata.namespace),
            "logical_name": metadata.logical_name,
            "category": metadata.category.value,
            "media_type": metadata.media_type,
            "declared_media_type": metadata.declared_media_type,
            "size_bytes": metadata.size_bytes,
            "checksum_algorithm": metadata.checksum.algorithm.value,
            "checksum_digest": metadata.checksum.digest,
            "classification": metadata.classification.value,
            "retention_policy_ref": metadata.retention_policy_ref,
            "state": metadata.state.value,
            "version": metadata.version,
            "created_at": metadata.created_at.isoformat(),
            "available_at": metadata.available_at.isoformat() if metadata.available_at else None,
            "expires_at": metadata.expires_at.isoformat() if metadata.expires_at else None,
            "storage_object_ref": record.storage_object_ref,
        }

    def _persist(self, record: ArtifactMetadataRecord, *, previous_version: int | None, idempotency_key: str) -> None:
        context = PersistenceOperationContext(record.context.user_id, record.context.workspace_id, record.context.agent_id, record.context.execution_id, record.context.correlation_id, str(record.context.purpose), record.context.actor)
        ref = RecordReference(record.metadata.artifact_id)
        change = RecordChange(ref, "artifact_metadata", previous_version, self._data(record), DataClassification(record.metadata.classification))
        expected = () if previous_version is None else (ExpectedVersion(ref, previous_version),)
        audit = AuditChange(f"audit:{record.metadata.artifact_id}:{record.metadata.version}", ref, "ARTIFACT_METADATA_MUTATION", record.metadata.version)
        request = TransactionRequest(
            transaction_id=f"artifact-tx:{record.metadata.artifact_id}:{record.metadata.version}",
            context=context,
            options=TransactionOptions(),
            idempotency_key=idempotency_key,
            fingerprint=hashlib.sha256(json.dumps(self._data(record), sort_keys=True).encode()).hexdigest(),
            expected_versions=expected,
            changes=(change,),
            audit=(audit,),
            outbox=(),
        )
        result = self.persistence.transact(request)
        if not isinstance(result, TransactionCommitted):
            raise ArtifactError(ArtifactErrorCode.IO_UNAVAILABLE, Retryability.AFTER_RECONCILIATION, EffectState.UNKNOWN)

    def create_staging(self, record: ArtifactMetadataRecord, *, reservation_bytes: int, idempotency_key: str):
        result = super().create_staging(record, reservation_bytes=reservation_bytes, idempotency_key=idempotency_key)
        if isinstance(result, ArtifactError):
            return result
        try:
            self._persist(record, previous_version=None, idempotency_key=idempotency_key)
        except ArtifactError as error:
            return error
        return result

    def publish_available(self, context, artifact_id, *, size_bytes, checksum, idempotency_key):
        current = self.get(context, artifact_id)
        result = super().publish_available(context, artifact_id, size_bytes=size_bytes, checksum=checksum, idempotency_key=idempotency_key)
        if isinstance(result, ArtifactError) or current is None:
            return result
        try:
            self._persist(result.record, previous_version=current.metadata.version, idempotency_key=idempotency_key)
        except ArtifactError as error:
            return error
        return result


__all__ = ["TransactionalArtifactMetadataRepository"]
