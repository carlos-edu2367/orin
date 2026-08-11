from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import (
    CreateWorkspaceContext,
    EffectState,
    FilesystemObjectIdentity,
    OpaqueRootHandleRef,
    OpaqueWorkspaceRootRef,
    RootHealth,
    WorkspaceError,
    WorkspaceErrorCode,
    WorkspaceOperationContext,
    WorkspaceRootDescriptor,
)
from .ports import RootCleanupResult, RootInspection, RootResolution


@dataclass
class _RootState:
    descriptor: WorkspaceRootDescriptor
    issue: str | None = None
    entries: int = 0
    bytes: int = 0
    handles: set[str] | None = None

    def __post_init__(self) -> None:
        self.handles = set()


class InMemoryWorkspaceRootAdapter:
    """Reference root adapter with an opaque logical model and fault injection."""

    def __init__(self) -> None:
        self._counter = 0
        self._roots: dict[str, _RootState] = {}
        self.on_resolve = None

    def _next(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}:{self._counter}"

    def provision(self, context: CreateWorkspaceContext, workspace_id: str) -> WorkspaceRootDescriptor:
        now = datetime.now(timezone.utc)
        descriptor = WorkspaceRootDescriptor(
            workspace_id=workspace_id,
            root_ref=OpaqueWorkspaceRootRef(self._next("root")),
            root_identity=FilesystemObjectIdentity(self._next("identity")),
            storage_class="LOCAL_REFERENCE",
            containment_policy_version=1,
            provisioned_at=now,
            health=RootHealth.READY,
        )
        self._roots[workspace_id] = _RootState(descriptor)
        return descriptor

    def _state_for(self, descriptor: WorkspaceRootDescriptor) -> _RootState | None:
        state = self._roots.get(descriptor.workspace_id)
        if state is None or state.descriptor.root_ref != descriptor.root_ref or state.descriptor.root_identity != descriptor.root_identity:
            return None
        return state

    def resolve(self, context: WorkspaceOperationContext, descriptor: WorkspaceRootDescriptor) -> RootResolution:
        state = self._state_for(descriptor)
        if state is None or context.workspace_id != descriptor.workspace_id:
            return RootResolution(None, descriptor.root_identity, RootHealth.MISSING, "root missing")
        if state.issue is not None:
            health = RootHealth.MISSING if state.issue == "MISSING" else RootHealth.QUARANTINED
            return RootResolution(None, state.descriptor.root_identity, health, state.issue)
        handle_value = self._next("handle")
        state.handles.add(handle_value)
        handle = OpaqueRootHandleRef(handle_value, binding=f"{context.workspace_id}:{descriptor.root_identity!r}")
        if self.on_resolve is not None:
            self.on_resolve(context.workspace_id)
        return RootResolution(handle, state.descriptor.root_identity, RootHealth.READY)

    def release(self, handle: OpaqueRootHandleRef) -> None:
        for state in self._roots.values():
            state.handles.discard(handle._value)

    def inspect(self, descriptor: WorkspaceRootDescriptor, maximum_entries: int) -> RootInspection:
        state = self._state_for(descriptor)
        if state is None:
            return RootInspection(descriptor.root_identity, RootHealth.MISSING, 0, 0, "MISSING")
        if state.issue is not None:
            health = RootHealth.MISSING if state.issue == "MISSING" else RootHealth.QUARANTINED
            return RootInspection(state.descriptor.root_identity, health, 0, 0, state.issue)
        return RootInspection(state.descriptor.root_identity, RootHealth.READY, min(state.entries, maximum_entries), state.bytes)

    def cleanup(self, context: WorkspaceOperationContext, descriptor: WorkspaceRootDescriptor, maximum_entries: int, checkpoint: str | None = None) -> RootCleanupResult:
        state = self._state_for(descriptor)
        if state is None or context.workspace_id != descriptor.workspace_id:
            return RootCleanupResult(EffectState.UNKNOWN, 0, checkpoint, RootHealth.MISSING, "root mismatch")
        if state.issue is not None:
            return RootCleanupResult(EffectState.UNKNOWN, 0, checkpoint, RootHealth.QUARANTINED, state.issue)
        if maximum_entries < 1:
            return RootCleanupResult(EffectState.NOT_APPLIED, 0, checkpoint, RootHealth.READY, "entry limit invalid")
        processed = min(state.entries, maximum_entries)
        state.entries -= processed
        if state.entries == 0:
            state.bytes = 0
            return RootCleanupResult(EffectState.APPLIED, processed, None, RootHealth.READY)
        next_checkpoint = f"checkpoint:{state.entries}"
        return RootCleanupResult(EffectState.APPLIED, processed, next_checkpoint, RootHealth.READY)

    def seed_entries(self, workspace_id: str, *, entries: int, bytes_count: int) -> None:
        state = self._roots[workspace_id]
        state.entries = entries
        state.bytes = bytes_count

    def set_issue(self, workspace_id: str, issue: str) -> None:
        state = self._roots[workspace_id]
        state.issue = issue

    def clear_issue(self, workspace_id: str) -> None:
        self._roots[workspace_id].issue = None

    def swap_identity(self, workspace_id: str) -> None:
        state = self._roots[workspace_id]
        state.descriptor = WorkspaceRootDescriptor(
            workspace_id=state.descriptor.workspace_id,
            root_ref=state.descriptor.root_ref,
            root_identity=FilesystemObjectIdentity(self._next("identity-swapped")),
            storage_class=state.descriptor.storage_class,
            containment_policy_version=state.descriptor.containment_policy_version,
            provisioned_at=state.descriptor.provisioned_at,
            health=RootHealth.QUARANTINED,
        )

    def remove_root(self, workspace_id: str) -> None:
        self._roots[workspace_id].issue = "MISSING"

    def finalize_delete(self, workspace_id: str, descriptor: WorkspaceRootDescriptor) -> bool:
        state = self._state_for(descriptor)
        if state is None or state.entries != 0 or state.issue is not None:
            return False
        state.issue = "MISSING"
        state.descriptor = WorkspaceRootDescriptor(
            workspace_id=descriptor.workspace_id,
            root_ref=descriptor.root_ref,
            root_identity=descriptor.root_identity,
            storage_class=descriptor.storage_class,
            containment_policy_version=descriptor.containment_policy_version,
            provisioned_at=descriptor.provisioned_at,
            health=RootHealth.MISSING,
        )
        return True


__all__ = ["InMemoryWorkspaceRootAdapter"]
