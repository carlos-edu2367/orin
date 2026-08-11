from __future__ import annotations

import hashlib
import json
from threading import RLock

from .models import (
    CreateWorkspace,
    WorkspaceError,
    WorkspaceErrorCode,
    WorkspaceRecord,
    WorkspaceState,
    WorkspaceOperationContext,
)


class InMemoryWorkspaceRegistry:
    """Reference durable registry: ownership and tombstones are authoritative."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, WorkspaceRecord] = {}
        self._tombstones: set[str] = set()
        self._idempotency: dict[tuple[tuple[str, ...], str], tuple[str, WorkspaceRecord]] = {}

    @staticmethod
    def _fingerprint(command: CreateWorkspace, record: WorkspaceRecord) -> str:
        payload = {
            "workspace_id": record.workspace_id,
            "user_id": record.user_id,
            "display_name": record.display_name,
            "quota": record.quota,
            "configuration_ref": record.configuration_ref,
            "classification": record.classification.value,
        }
        return hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()

    def create(self, command: CreateWorkspace, record: WorkspaceRecord) -> WorkspaceRecord | WorkspaceError:
        if record.state is not WorkspaceState.PROVISIONING or record.version != 1:
            return WorkspaceError(WorkspaceErrorCode.INVALID_REQUEST)
        if command.context.requested_workspace_id != record.workspace_id:
            return WorkspaceError(WorkspaceErrorCode.INVALID_REQUEST)
        fingerprint = self._fingerprint(command, record)
        key = (command.context.scope_key(), command.idempotency_key)
        with self._lock:
            previous = self._idempotency.get(key)
            if previous is not None:
                if previous[0] != fingerprint:
                    return WorkspaceError(WorkspaceErrorCode.IDEMPOTENCY_CONFLICT)
                return previous[1]
            if record.workspace_id in self._records or record.workspace_id in self._tombstones:
                return WorkspaceError(WorkspaceErrorCode.ID_UNAVAILABLE)
            self._records[record.workspace_id] = record
            self._idempotency[key] = (fingerprint, record)
            return record

    def get(self, context: WorkspaceOperationContext) -> WorkspaceRecord | None:
        with self._lock:
            record = self._records.get(context.workspace_id)
            if record is None or record.user_id != context.user_id:
                return None
            return record

    def get_by_id(self, workspace_id: str) -> WorkspaceRecord | None:
        with self._lock:
            return self._records.get(workspace_id)

    def replace(self, record: WorkspaceRecord, *, expected_version: int) -> WorkspaceRecord | WorkspaceError:
        with self._lock:
            current = self._records.get(record.workspace_id)
            if current is None:
                return WorkspaceError(WorkspaceErrorCode.NOT_FOUND)
            if current.version != expected_version:
                return WorkspaceError(WorkspaceErrorCode.VERSION_CONFLICT)
            if record.version != expected_version + 1:
                return WorkspaceError(WorkspaceErrorCode.INVALID_REQUEST)
            self._records[record.workspace_id] = record
            for key, (fingerprint, stored) in tuple(self._idempotency.items()):
                if stored.workspace_id == record.workspace_id:
                    self._idempotency[key] = (fingerprint, record)
            if record.state is WorkspaceState.DELETED:
                self._tombstones.add(record.workspace_id)
            return record

    def delete_tombstone_exists(self, workspace_id: str) -> bool:
        with self._lock:
            return workspace_id in self._tombstones

    def snapshot_records(self) -> tuple[WorkspaceRecord, ...]:
        with self._lock:
            return tuple(self._records.values())


__all__ = ["InMemoryWorkspaceRegistry"]
