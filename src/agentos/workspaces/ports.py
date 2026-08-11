from __future__ import annotations

from typing import Protocol

from .models import (
    AcquireWorkspaceLease,
    ActivateWorkspace,
    CreateWorkspace,
    CreateWorkspaceContext,
    DeleteWorkspace,
    InspectWorkspace,
    OpaqueRootHandleRef,
    OpaqueWorkspaceRootRef,
    RecordWorkspaceUsage,
    ReconcileWorkspace,
    ReleaseQuotaReservation,
    ReleaseWorkspaceLease,
    RenewWorkspaceLease,
    ReserveWorkspaceUsage,
    TransitionWorkspace,
    WorkspaceError,
    WorkspaceLease,
    WorkspaceOperationContext,
    WorkspaceRecord,
    WorkspaceRootDescriptor,
    WorkspaceSnapshot,
)


class WorkspaceRegistry(Protocol):
    def create(self, command: CreateWorkspace, record: WorkspaceRecord) -> WorkspaceRecord | WorkspaceError: ...
    def get(self, context: WorkspaceOperationContext) -> WorkspaceRecord | None: ...
    def get_by_id(self, workspace_id: str) -> WorkspaceRecord | None: ...
    def replace(self, record: WorkspaceRecord, *, expected_version: int) -> WorkspaceRecord | WorkspaceError: ...


class WorkspaceRootAdapter(Protocol):
    def provision(self, context: CreateWorkspaceContext, workspace_id: str) -> WorkspaceRootDescriptor | WorkspaceError: ...
    def resolve(self, context: WorkspaceOperationContext, descriptor: WorkspaceRootDescriptor) -> "RootResolution": ...
    def release(self, handle: OpaqueRootHandleRef) -> None: ...
    def inspect(self, descriptor: WorkspaceRootDescriptor, maximum_entries: int) -> "RootInspection": ...
    def cleanup(self, context: WorkspaceOperationContext, descriptor: WorkspaceRootDescriptor, maximum_entries: int, checkpoint: str | None = None) -> "RootCleanupResult": ...
    def finalize_delete(self, workspace_id: str, descriptor: WorkspaceRootDescriptor) -> bool: ...


class WorkspaceEventSink(Protocol):
    def append(self, event) -> None: ...


class WorkspaceManager(Protocol):
    def create(self, command: CreateWorkspace): ...
    def activate(self, command: ActivateWorkspace): ...
    def inspect(self, query: InspectWorkspace): ...
    def transition(self, command: TransitionWorkspace): ...
    def acquire_lock(self, command): ...
    def release_lock(self, lock_id: str, context: WorkspaceOperationContext, fencing_token): ...
    def assert_fence(self, context: WorkspaceOperationContext, fencing_token): ...
    def reserve_usage(self, request: ReserveWorkspaceUsage): ...
    def record_usage(self, request: RecordWorkspaceUsage): ...
    def release_reservation(self, request: ReleaseQuotaReservation): ...
    def delete(self, command: DeleteWorkspace): ...
    def reconcile(self, command: ReconcileWorkspace): ...


class RootResolution:
    __slots__ = ("handle", "identity", "health", "reason")

    def __init__(self, handle: OpaqueRootHandleRef | None, identity, health, reason: str | None = None) -> None:
        self.handle = handle
        self.identity = identity
        self.health = health
        self.reason = reason


class RootInspection:
    __slots__ = ("identity", "health", "entries", "bytes", "unsafe_reason")

    def __init__(self, identity, health, entries: int, bytes: int, unsafe_reason: str | None = None) -> None:
        self.identity = identity
        self.health = health
        self.entries = entries
        self.bytes = bytes
        self.unsafe_reason = unsafe_reason


class RootCleanupResult:
    __slots__ = ("effect_state", "processed_entries", "checkpoint", "health", "reason")

    def __init__(self, effect_state, processed_entries: int, checkpoint: str | None, health, reason: str | None = None) -> None:
        self.effect_state = effect_state
        self.processed_entries = processed_entries
        self.checkpoint = checkpoint
        self.health = health
        self.reason = reason


__all__ = ["WorkspaceEventSink", "WorkspaceManager", "WorkspaceRegistry", "WorkspaceRootAdapter", "RootCleanupResult", "RootInspection", "RootResolution"]
