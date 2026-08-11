from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json

from agentos.events.models import DataClassification, EventEnvelope
from agentos.persistence import (
    AuditChange,
    AuthorizedRead,
    ExpectedVersion,
    OutboxChange,
    PersistenceOperationContext,
    RecordChange,
    RecordReference,
    TransactionCommitted,
    TransactionOptions,
    TransactionRequest,
    TransactionalPersistence,
)

from .models import (
    CreateWorkspace,
    OpaqueWorkspaceRootRef,
    FilesystemObjectIdentity,
    RootHealth,
    WorkspaceError,
    WorkspaceErrorCode,
    FencingToken,
    WorkspaceOperationContext,
    WorkspaceRecord,
    WorkspaceRootDescriptor,
    WorkspaceState,
    WorkspaceUsage,
    UsageReconciliationState,
)
from .registry import InMemoryWorkspaceRegistry


class TransactionalWorkspaceRegistry(InMemoryWorkspaceRegistry):
    """Workspace registry composed exclusively with RFC 601's public port."""

    events_are_transactional = True

    def __init__(self, persistence: TransactionalPersistence) -> None:
        super().__init__()
        self.persistence = persistence
        self._contexts: dict[str, WorkspaceOperationContext] = {}

    @staticmethod
    def _persistence_context(context: WorkspaceOperationContext) -> PersistenceOperationContext:
        return PersistenceOperationContext(context.user_id, context.workspace_id, context.agent_id, context.execution_id, context.correlation_id, "workspace.registry", context.actor)

    @staticmethod
    def _data(record: WorkspaceRecord, context: WorkspaceOperationContext) -> dict[str, object]:
        descriptor = record.root_descriptor
        return {
            "workspace_id": record.workspace_id,
            "user_id": record.user_id,
            "display_name": record.display_name,
            "state": record.state.value,
            "root_ref": descriptor.root_ref._value if descriptor else None,
            "root_identity": descriptor.root_identity._value if descriptor else None,
            "storage_class": descriptor.storage_class if descriptor else None,
            "containment_policy_version": descriptor.containment_policy_version if descriptor else None,
            "root_provisioned_at": descriptor.provisioned_at.isoformat() if descriptor else None,
            "root_health": descriptor.health.value if descriptor else None,
            "maximum_bytes": record.quota.maximum_bytes,
            "maximum_entries": record.quota.maximum_entries,
            "maximum_file_bytes": record.quota.maximum_file_bytes,
            "maximum_depth": record.quota.maximum_depth,
            "maximum_active_leases": record.quota.maximum_active_leases,
            "quota_reserved_bytes": record.quota.reserved_bytes,
            "configuration_ref": record.configuration_ref,
            "classification": record.classification.value,
            "version": record.version,
            "accounted_bytes": record.usage.accounted_bytes,
            "accounted_entries": record.usage.accounted_entries,
            "usage_reserved_bytes": record.usage.reserved_bytes,
            "usage_reserved_entries": record.usage.reserved_entries,
            "active_leases": record.usage.active_leases,
            "measured_at": record.usage.measured_at.isoformat(),
            "usage_state": record.usage.reconciliation_state.value,
            "created_at": record.created_at.isoformat(),
            "activated_at": record.activated_at.isoformat() if record.activated_at else None,
            "archived_at": record.archived_at.isoformat() if record.archived_at else None,
            "deletion_requested_at": record.deletion_requested_at.isoformat() if record.deletion_requested_at else None,
            "deleted_at": record.deleted_at.isoformat() if record.deleted_at else None,
            "creation_idempotency_key": record.creation_idempotency_key,
            "creation_fingerprint": record.creation_fingerprint,
            "deletion_fence": record.deletion_fence._value if record.deletion_fence else None,
            "deletion_checkpoint": record.deletion_checkpoint,
            "agent_id": context.agent_id,
            "execution_id": context.execution_id,
            "correlation_id": context.correlation_id,
            "purpose": context.purpose,
            "actor": context.actor,
        }

    @staticmethod
    def _from_data(data: dict[str, object]) -> tuple[WorkspaceRecord, WorkspaceOperationContext]:
        from .models import WorkspaceQuota
        parse_time = lambda key: datetime.fromisoformat(str(data[key])) if data.get(key) else None
        descriptor = None
        if data.get("root_ref") and data.get("root_identity"):
            descriptor = WorkspaceRootDescriptor(str(data["workspace_id"]), OpaqueWorkspaceRootRef(str(data["root_ref"])), FilesystemObjectIdentity(str(data["root_identity"])), str(data["storage_class"]), int(data["containment_policy_version"]), datetime.fromisoformat(str(data["root_provisioned_at"])), RootHealth(str(data["root_health"])))
        context = WorkspaceOperationContext(str(data["user_id"]), str(data["workspace_id"]), str(data["agent_id"]), str(data["execution_id"]), str(data["correlation_id"]), str(data["purpose"]), str(data["actor"]))
        record = WorkspaceRecord(
            workspace_id=str(data["workspace_id"]),
            user_id=str(data["user_id"]),
            display_name=str(data["display_name"]),
            state=WorkspaceState(str(data["state"])),
            root_descriptor=descriptor,
            quota=WorkspaceQuota(int(data["maximum_bytes"]), int(data["maximum_entries"]), int(data["maximum_file_bytes"]), int(data["maximum_depth"]), int(data["maximum_active_leases"]), int(data["quota_reserved_bytes"])),
            configuration_ref=str(data["configuration_ref"]),
            classification=DataClassification(str(data["classification"])),
            version=int(data["version"]),
            usage=WorkspaceUsage(int(data["accounted_bytes"]), int(data["accounted_entries"]), int(data["usage_reserved_bytes"]), int(data["active_leases"]), datetime.fromisoformat(str(data["measured_at"])), UsageReconciliationState(str(data["usage_state"])), int(data.get("usage_reserved_entries", 0))),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            activated_at=parse_time("activated_at"),
            archived_at=parse_time("archived_at"),
            deletion_requested_at=parse_time("deletion_requested_at"),
            deleted_at=parse_time("deleted_at"),
            creation_idempotency_key=str(data["creation_idempotency_key"]) if data.get("creation_idempotency_key") else None,
            creation_fingerprint=str(data["creation_fingerprint"]) if data.get("creation_fingerprint") else None,
            deletion_fence=FencingToken(int(data["deletion_fence"])) if data.get("deletion_fence") is not None else None,
            deletion_checkpoint=str(data["deletion_checkpoint"]) if data.get("deletion_checkpoint") else None,
        )
        return record, context

    @staticmethod
    def _event_type(record: WorkspaceRecord, previous: WorkspaceRecord | None) -> str | None:
        if previous is None:
            return "WorkspaceProvisioningStarted"
        return {
            WorkspaceState.ACTIVE: "WorkspaceActivated",
            WorkspaceState.SUSPENDED: "WorkspaceSuspended",
            WorkspaceState.ARCHIVED: "WorkspaceArchived",
            WorkspaceState.DELETING: "WorkspaceDeletionStarted",
            WorkspaceState.DELETED: "WorkspaceDeleted",
            WorkspaceState.RECOVERY_REQUIRED: "WorkspaceRecoveryRequired",
        }.get(record.state)

    def _persist(self, record: WorkspaceRecord, context: WorkspaceOperationContext, previous: WorkspaceRecord | None, idempotency_key: str):
        persistence_context = self._persistence_context(context)
        reference = RecordReference(record.workspace_id)
        data = self._data(record, context)
        event_type = self._event_type(record, previous)
        outbox = ()
        if event_type:
            event = EventEnvelope(
                event_id=f"workspace-event:{record.workspace_id}:{record.version}:{event_type}",
                event_type=event_type,
                event_version=1,
                occurred_at=record.created_at,
                source="workspaces",
                correlation_id=context.correlation_id,
                causation_id=None,
                sequence=record.version,
                user_id=record.user_id,
                workspace_id=record.workspace_id,
                execution_id=context.execution_id,
                agent_id=context.agent_id,
                classification=record.classification,
                payload={"workspace_id": record.workspace_id, "user_id": record.user_id, "state": record.state.value, "version": record.version, "policy_version": record.root_descriptor.containment_policy_version if record.root_descriptor else 1, "purpose": "workspace.registry"},
            )
            outbox = (OutboxChange(event, reference, record.version),)
        changes = [RecordChange(reference, "workspace", None if previous is None else previous.version, data, record.classification)]
        audits = [AuditChange(f"audit:{record.workspace_id}:{record.version}", reference, "WORKSPACE_MUTATION", record.version)]
        if previous is None and record.creation_idempotency_key:
            creation_ref = RecordReference(self._creation_ref(record.user_id, record.creation_idempotency_key))
            changes.append(RecordChange(creation_ref, "workspace_creation", None, {"user_id": record.user_id, "creation_idempotency_key": record.creation_idempotency_key, "creation_fingerprint": record.creation_fingerprint or "", "allocated_workspace_id": record.workspace_id}, record.classification))
            audits.append(AuditChange(f"audit:workspace-creation:{record.workspace_id}", creation_ref, "WORKSPACE_CREATE_IDEMPOTENCY", 1))
        request = TransactionRequest(
            transaction_id=f"workspace-tx:{record.workspace_id}:{record.version}",
            context=persistence_context,
            options=TransactionOptions(),
            idempotency_key=idempotency_key,
            fingerprint=hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest(),
            expected_versions=() if previous is None else (ExpectedVersion(reference, previous.version),),
            changes=tuple(changes),
            audit=tuple(audits),
            outbox=outbox,
        )
        result = self.persistence.transact(request)
        if not isinstance(result, TransactionCommitted):
            return WorkspaceError(WorkspaceErrorCode.UNKNOWN, effect_state="UNKNOWN", retryability="AFTER_RECONCILIATION")
        return None

    def record_fact(self, event: EventEnvelope, context: WorkspaceOperationContext, record: WorkspaceRecord):
        """Persist an event-only fact through RFC 601 without mutating Workspace state."""
        reference = RecordReference(f"workspace-fact:{event.event_id}")
        persistence_context = self._persistence_context(context)
        request = TransactionRequest(
            transaction_id=f"workspace-fact-tx:{event.event_id}",
            context=persistence_context,
            options=TransactionOptions(),
            idempotency_key=f"fact:{event.event_id}",
            fingerprint=hashlib.sha256(f"{event.event_id}:{record.workspace_id}".encode()).hexdigest(),
            expected_versions=(),
            changes=(RecordChange(reference, "workspace_fact", None, {"event_type": event.event_type, "workspace_id": record.workspace_id, "version": record.version, "reason_code": event.payload.get("reason_code")}, record.classification),),
            audit=(AuditChange(f"audit:{event.event_id}", reference, "WORKSPACE_FACT", 1),),
            outbox=(OutboxChange(replace(event, sequence=1), reference, 1),),
        )
        result = self.persistence.transact(request)
        if not isinstance(result, TransactionCommitted):
            return WorkspaceError(WorkspaceErrorCode.UNKNOWN, effect_state="UNKNOWN", retryability="AFTER_RECONCILIATION")
        return None

    def create(self, command: CreateWorkspace, record: WorkspaceRecord):
        persisted = self.find_creation(command.context, command.idempotency_key)
        if persisted is not None:
            existing = self.get_by_id(persisted[0]) or self._read_by_id(command.context, persisted[0])
            if existing is not None:
                if persisted[1] != record.creation_fingerprint:
                    return WorkspaceError(WorkspaceErrorCode.IDEMPOTENCY_CONFLICT)
                return existing
        existing = self.get_by_id(record.workspace_id)
        if existing is not None:
            return super().create(command, record)
        context = WorkspaceOperationContext(command.context.user_id, record.workspace_id, command.context.agent_id, command.context.execution_id, command.context.correlation_id, "workspace.registry", command.context.actor)
        error = self._persist(record, context, None, command.idempotency_key)
        if error:
            return error
        self._contexts[record.workspace_id] = context
        return super().create(command, record)

    @staticmethod
    def _creation_ref(user_id: str, idempotency_key: str) -> str:
        return "workspace-creation:" + hashlib.sha256(f"{user_id}\x00{idempotency_key}".encode()).hexdigest()

    def find_creation(self, context, idempotency_key: str):
        pctx = PersistenceOperationContext(context.user_id, None, context.agent_id, context.execution_id, context.correlation_id, "workspace.registry", context.actor)
        result = self.persistence.read(AuthorizedRead(pctx, RecordReference(self._creation_ref(context.user_id, idempotency_key)), "workspace_creation", DataClassification.RESTRICTED))
        if getattr(result, "data", None) is None:
            return None
        data = dict(result.data)
        return str(data["allocated_workspace_id"]), str(data["creation_fingerprint"])

    def _read_by_id(self, context, workspace_id: str):
        operation = WorkspaceOperationContext(context.user_id, workspace_id, context.agent_id, context.execution_id, context.correlation_id, "workspace.registry", context.actor)
        result = self.persistence.read(AuthorizedRead(self._persistence_context(operation), RecordReference(workspace_id), "workspace", DataClassification.RESTRICTED))
        if getattr(result, "data", None) is None:
            return None
        record, stored_context = self._from_data(dict(result.data))
        self._records[record.workspace_id] = record
        self._contexts[record.workspace_id] = stored_context
        return record

    def replace(self, record: WorkspaceRecord, *, expected_version: int):
        previous = self.get_by_id(record.workspace_id)
        if previous is None or previous.version != expected_version:
            return super().replace(record, expected_version=expected_version)
        context = self._contexts.get(record.workspace_id)
        if context is None:
            context = WorkspaceOperationContext(record.user_id, record.workspace_id, "system:workspace", "execution:workspace", "correlation:workspace", "workspace.registry", f"system:{record.user_id}")
        error = self._persist(record, context, previous, f"replace:{record.workspace_id}:{record.version}")
        if error:
            return error
        return super().replace(record, expected_version=expected_version)

    def get(self, context: WorkspaceOperationContext):
        local = super().get(context)
        if local is not None:
            return local
        result = self.persistence.read(AuthorizedRead(self._persistence_context(context), RecordReference(context.workspace_id), "workspace", DataClassification.RESTRICTED))
        if getattr(result, "data", None) is None:
            return None
        record, stored_context = self._from_data(dict(result.data))
        self._records[record.workspace_id] = record
        self._contexts[record.workspace_id] = stored_context
        return record


__all__ = ["TransactionalWorkspaceRegistry"]
