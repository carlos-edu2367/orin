from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from agentos.events.models import DataClassification, EventEnvelope

from .adapters import BrowserResourceAdapter, FilesystemResourceAdapter, TerminalResourceAdapter
from .models import (
    AuthorizeResourceOperation,
    AuthorizedResourceHandle,
    CleanupResult,
    EffectState,
    IsolationMode,
    ReconciliationResult,
    RenewResourceLease,
    ResourceBudget,
    ResourceCapability,
    ResourceDescriptor,
    ResourceError,
    ResourceErrorCode,
    ResourceHealth,
    ResourceLease,
    ResourceLeaseRequest,
    ResourceLeaseState,
    ResourceOperationContext,
    ResourceType,
    RevokeResourceLease,
    ReleaseResourceLease,
    isolation_fingerprint,
)
from .ports import ResourceAdapter
from .security import same_binding, validate_actor


class InMemoryResourceEventSink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    def append(self, event: EventEnvelope) -> None:
        self.events.append(event)


class ResourceManagerService:
    def __init__(self, *, workspace_manager=None, event_sink=None, clock=None) -> None:
        self.workspace_manager = workspace_manager
        self.event_sink = event_sink or InMemoryResourceEventSink()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._counter = 0
        self._fence = 0
        self._leases: dict[str, ResourceLease] = {}
        self._bindings: dict[str, tuple[object, object, ResourceOperationContext, str]] = {}
        self._idempotency: dict[tuple[tuple[str, ...], str], ResourceLease] = {}
        self._sequences: dict[str, int] = {}
        self._descriptors: dict[ResourceType, ResourceDescriptor] = {}
        self._adapters: dict[ResourceType, ResourceAdapter] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(ResourceDescriptor(ResourceType.FILESYSTEM, "filesystem.reference", tuple(ResourceCapability(cap) for cap in ResourceCapability if cap.value.startswith("FILESYSTEM_")) + (ResourceCapability.INSPECT,), (IsolationMode.WORKSPACE,), __import__("agentos.resources.models", fromlist=["ResourceLimits"]).ResourceLimits()), FilesystemResourceAdapter())
        self.register(ResourceDescriptor(ResourceType.TERMINAL, "terminal.reference", (ResourceCapability.TERMINAL_SESSION, ResourceCapability.TERMINAL_CANCEL, ResourceCapability.INSPECT), (IsolationMode.PROCESS, IsolationMode.WORKSPACE), __import__("agentos.resources.models", fromlist=["ResourceLimits"]).ResourceLimits()), TerminalResourceAdapter())
        self.register(ResourceDescriptor(ResourceType.BROWSER, "browser.reference", (ResourceCapability.BROWSER_SESSION, ResourceCapability.BROWSER_NAVIGATE, ResourceCapability.BROWSER_CANCEL, ResourceCapability.INSPECT), (IsolationMode.SESSION, IsolationMode.WORKSPACE), __import__("agentos.resources.models", fromlist=["ResourceLimits"]).ResourceLimits()), BrowserResourceAdapter())

    def now(self) -> datetime:
        return self._clock()

    def register(self, descriptor: ResourceDescriptor, adapter: ResourceAdapter) -> None:
        with self._lock:
            self._descriptors[descriptor.resource_type] = descriptor
            self._adapters[descriptor.resource_type] = adapter

    def descriptor(self, resource_type: ResourceType) -> ResourceDescriptor:
        return self._descriptors[ResourceType(resource_type)]

    def set_health(self, resource_type: ResourceType, health: ResourceHealth) -> None:
        with self._lock:
            self._descriptors[ResourceType(resource_type)] = replace(self.descriptor(resource_type), health=ResourceHealth(health))

    def fail_cleanup_next(self, resource_type: ResourceType) -> None:
        adapter = self._adapters[ResourceType(resource_type)]
        adapter.fail_cleanup = True

    def bind_filesystem_port(self, filesystem_port) -> None:
        adapter = self._adapters.get(ResourceType.FILESYSTEM)
        if hasattr(adapter, "bind_filesystem_port"):
            adapter.bind_filesystem_port(filesystem_port)

    def active_leases(self) -> tuple[ResourceLease, ...]:
        with self._lock:
            return tuple(lease for lease in self._leases.values() if lease.state is ResourceLeaseState.LEASED)

    def _event(self, event_type: str, lease: ResourceLease, *, reason: str | None = None) -> None:
        sequence = self._sequences.get(lease.context.execution_id, 0) + 1
        self._sequences[lease.context.execution_id] = sequence
        payload = {"lease_id": lease.lease_id, "resource_type": lease.resource_type.value, "resource_ref": lease.resource_ref, "outcome": lease.state.value}
        if reason:
            payload["reason_code"] = reason[:64]
        self.event_sink.append(EventEnvelope(event_id=f"resource-event:{lease.lease_id}:{sequence}", event_type=event_type, event_version=1, occurred_at=self.now(), source="resource_manager", correlation_id=lease.context.correlation_id, causation_id=lease.lease_id, sequence=sequence, user_id=lease.context.user_id, workspace_id=lease.context.workspace_id, agent_id=lease.context.agent_id, execution_id=lease.context.execution_id, classification=DataClassification.INTERNAL, payload=payload))

    def _error(self, code: ResourceErrorCode, reason: str = "resource operation failed", *, effect_state=EffectState.NOT_APPLIED) -> ResourceError:
        return ResourceError(code, reason, effect_state=effect_state)

    def _workspace_lease(self, request: ResourceLeaseRequest):
        if self.workspace_manager is None or request.resource_type is not ResourceType.FILESYSTEM:
            return None
        from agentos.workspaces.models import AcquireWorkspaceLease, InspectWorkspace, WorkspaceOperationBudget, WorkspacePermission
        workspace_context = self._workspace_context(request.context)
        current = self.workspace_manager.inspect(InspectWorkspace(workspace_context))
        if hasattr(current, "code"):
            return self._error(ResourceErrorCode.UNAUTHORIZED)
        permissions = (WorkspacePermission.READ,) if request.required_capabilities == (ResourceCapability.FILESYSTEM_READ,) else (WorkspacePermission.READ, WorkspacePermission.WRITE)
        return self.workspace_manager.acquire_lease(AcquireWorkspaceLease(request.request_id, workspace_context, permissions, request.requested_duration, WorkspaceOperationBudget(request.requested_budget.maximum_bytes, request.requested_budget.maximum_operations, 32), current.version, current.root_descriptor.root_identity, request.idempotency_key))

    @staticmethod
    def _workspace_context(context: ResourceOperationContext):
        from agentos.workspaces.models import WorkspaceOperationContext
        return WorkspaceOperationContext(context.user_id, context.workspace_id or "", context.agent_id, context.execution_id, context.correlation_id, "workspace.resource", context.actor)

    def acquire(self, request: ResourceLeaseRequest):
        try:
            validate_actor(request.context)
        except PermissionError:
            return self._error(ResourceErrorCode.UNAUTHORIZED)
        key = (request.context.scope_key(), request.idempotency_key)
        with self._lock:
            previous = self._idempotency.get(key)
            if previous is not None:
                return previous
            descriptor = self._descriptors.get(request.resource_type)
            if descriptor is None:
                return self._error(ResourceErrorCode.NOT_FOUND)
            if descriptor.health not in (ResourceHealth.AVAILABLE, ResourceHealth.DEGRADED):
                return self._error(ResourceErrorCode.HEALTH_REJECTED)
            if not set(request.required_capabilities).issubset(descriptor.capabilities) or not set(request.requested_permissions).issubset(descriptor.capabilities):
                return self._error(ResourceErrorCode.CAPABILITY_DENIED)
            if request.requested_duration > descriptor.limits.maximum_duration or request.requested_budget.maximum_duration > descriptor.limits.maximum_duration or request.requested_budget.maximum_operations > descriptor.limits.maximum_operations or request.requested_budget.maximum_bytes > descriptor.limits.maximum_bytes:
                return self._error(ResourceErrorCode.BUDGET_EXCEEDED)
            workspace_lease = self._workspace_lease(request)
            if isinstance(workspace_lease, ResourceError):
                return workspace_lease
            if workspace_lease is not None and hasattr(workspace_lease, "code"):
                return self._error(ResourceErrorCode.UNAUTHORIZED)
            self._counter += 1
            self._fence += 1
            lease_id = f"lease:{self._counter}"
            resource_ref = f"resource:{request.resource_type.value.lower()}:{self._counter}"
            adapter = self._adapters[request.resource_type]
            isolation_key = isolation_fingerprint(request.context, descriptor)
            binding = adapter.allocate(lease_id=lease_id, descriptor=descriptor, context=request.context, isolation_key=isolation_key)
            if isinstance(binding, ResourceError):
                return binding
            acquired = self.now()
            lease = ResourceLease(lease_id, resource_ref, request.resource_type, request.context, request.requested_permissions, isolation_key, request.requested_budget, ResourceLeaseState.LEASED, acquired, acquired + request.requested_duration, self._fence, descriptor.adapter_ref, getattr(workspace_lease, "lease_id", None))
            self._leases[lease_id] = lease
            self._bindings[lease_id] = (binding, adapter, request.context, descriptor.adapter_ref)
            self._idempotency[key] = lease
            self._event("ResourceLeaseGranted", lease)
            return lease

    def _lease_for(self, lease_id: str, context: ResourceOperationContext):
        lease = self._leases.get(lease_id)
        if lease is None:
            return self._error(ResourceErrorCode.NOT_FOUND)
        if not same_binding(lease.context, context) or lease.context.correlation_id != context.correlation_id:
            return self._error(ResourceErrorCode.UNAUTHORIZED)
        if lease.state is ResourceLeaseState.LEASED and self.workspace_manager is not None and lease.workspace_lease_id is not None:
            workspace_lease = self.workspace_manager._lease_for(self._workspace_context(context), lease.workspace_lease_id)
            if hasattr(workspace_lease, "code") or workspace_lease.state.value != "ACTIVE":
                return self._error(ResourceErrorCode.UNAUTHORIZED)
        if lease.state is ResourceLeaseState.LEASED and self.now() >= lease.expires_at:
            expired = replace(lease, state=ResourceLeaseState.EXPIRED)
            self._leases[lease_id] = expired
            self._event("ResourceLeaseExpired", expired)
            lease = expired
        return lease

    def renew(self, request: RenewResourceLease):
        with self._lock:
            lease = self._lease_for(request.lease_id, request.context)
            if isinstance(lease, ResourceError):
                return lease
            if lease.state is not ResourceLeaseState.LEASED:
                return self._error(ResourceErrorCode.LEASE_EXPIRED if lease.state is ResourceLeaseState.EXPIRED else ResourceErrorCode.LEASE_RELEASED)
            if lease.fencing_token != request.fencing_token or lease.expires_at != request.expected_expires_at:
                return self._error(ResourceErrorCode.FENCE_REJECTED)
            descriptor = self.descriptor(lease.resource_type)
            if request.requested_extension <= timedelta(0) or request.requested_extension > descriptor.limits.maximum_duration:
                return self._error(ResourceErrorCode.BUDGET_EXCEEDED)
            updated = replace(lease, expires_at=lease.expires_at + request.requested_extension)
            self._leases[lease.lease_id] = updated
            self._event("ResourceLeaseRenewed", updated)
            return updated

    def authorize(self, request: AuthorizeResourceOperation):
        with self._lock:
            lease = self._lease_for(request.lease_id, request.context)
            if isinstance(lease, ResourceError):
                return lease
            if lease.state is ResourceLeaseState.EXPIRED:
                return self._error(ResourceErrorCode.LEASE_EXPIRED)
            if lease.state is ResourceLeaseState.RELEASED:
                return self._error(ResourceErrorCode.LEASE_RELEASED)
            if lease.state is not ResourceLeaseState.LEASED:
                return self._error(ResourceErrorCode.LEASE_REVOKED)
            if request.capability not in lease.permissions:
                return self._error(ResourceErrorCode.CAPABILITY_DENIED)
            if request.requested_usage_operations < 1 or request.requested_usage_bytes < 0 or lease.usage_operations + request.requested_usage_operations > lease.budget.maximum_operations or lease.usage_bytes + request.requested_usage_bytes > lease.budget.maximum_bytes:
                return self._error(ResourceErrorCode.BUDGET_EXCEEDED)
            binding, adapter, _, _ = self._bindings[lease.lease_id]
            handle = AuthorizedResourceHandle(f"handle:{uuid4().hex}", lease.lease_id, request.operation_id, (request.capability,), lease.expires_at)
            self._bindings[handle.handle_ref] = (binding, adapter, request.context, request.operation_id)
            self._leases[lease.lease_id] = replace(lease, usage_operations=lease.usage_operations + request.requested_usage_operations, usage_bytes=lease.usage_bytes + request.requested_usage_bytes)
            return handle

    def _finish(self, request_context, lease_id: str, fencing_token: int, target_state: ResourceLeaseState, event_type: str, reason: str, deadline):
        raw = self._leases.get(lease_id)
        if raw is not None and same_binding(raw.context, request_context) and raw.state in (ResourceLeaseState.RELEASED, ResourceLeaseState.REVOKED):
            return CleanupResult(lease_id, raw.state.value, EffectState.APPLIED)
        lease = self._lease_for(lease_id, request_context)
        if isinstance(lease, ResourceError):
            return lease
        if lease.state is target_state or lease.state in (ResourceLeaseState.RELEASED, ResourceLeaseState.REVOKED) and target_state in (ResourceLeaseState.RELEASED, ResourceLeaseState.REVOKED):
            return CleanupResult(lease_id, lease.state.value, EffectState.APPLIED)
        if lease.fencing_token != fencing_token:
            return self._error(ResourceErrorCode.FENCE_REJECTED)
        binding, adapter, _, _ = self._bindings[lease_id]
        if target_state is ResourceLeaseState.REVOKED:
            adapter.signal_cancel(binding, reason)
        cleanup = adapter.cleanup(binding, deadline=deadline)
        if cleanup.effect_state is not EffectState.APPLIED:
            quarantined = replace(self.descriptor(lease.resource_type), health=ResourceHealth.QUARANTINED)
            self._descriptors[lease.resource_type] = quarantined
            pending = replace(lease, state=ResourceLeaseState.REVOKING, cleanup_confirmed=False)
            self._leases[lease_id] = pending
            self._event("ResourceCleanupFailed", pending, reason="CLEANUP_FAILED")
            return self._error(ResourceErrorCode.CLEANUP_FAILED, effect_state=EffectState.UNKNOWN)
        updated = replace(lease, state=target_state, released_at=self.now(), cleanup_confirmed=True)
        self._leases[lease_id] = updated
        if self.workspace_manager is not None and lease.workspace_lease_id is not None:
            from agentos.workspaces.models import ReleaseWorkspaceLease
            workspace_context = self._workspace_context(lease.context)
            workspace_lease = self.workspace_manager._lease_for(workspace_context, lease.workspace_lease_id)
            if hasattr(workspace_lease, "code"):
                return self._error(ResourceErrorCode.CLEANUP_FAILED, effect_state=EffectState.UNKNOWN)
            released = self.workspace_manager.release_lease(ReleaseWorkspaceLease(f"{lease_id}:workspace-release", workspace_context, lease.workspace_lease_id, workspace_lease.fencing_token, reason, f"{lease_id}:workspace-release"))
            if hasattr(released, "code"):
                return self._error(ResourceErrorCode.CLEANUP_FAILED, effect_state=EffectState.UNKNOWN)
        self._event("ResourceLeaseReleased" if target_state is ResourceLeaseState.RELEASED else "ResourceLeaseRevoked", updated, reason=reason)
        return CleanupResult(lease_id, target_state.value, EffectState.APPLIED)

    def release(self, request: ReleaseResourceLease):
        with self._lock:
            return self._finish(request.context, request.lease_id, request.fencing_token, ResourceLeaseState.RELEASED, "ResourceLeaseReleased", request.reason, self.now())

    def revoke(self, request: RevokeResourceLease):
        with self._lock:
            return self._finish(request.context, request.lease_id, request.fencing_token, ResourceLeaseState.REVOKED, "ResourceLeaseRevoked", request.reason, request.cleanup_deadline)

    def inspect(self, *, context: ResourceOperationContext, lease_id: str):
        with self._lock:
            return self._lease_for(lease_id, context)

    def validate_filesystem_handle(self, handle, *, lease_id: str, context: ResourceOperationContext, operation_id: str) -> bool:
        with self._lock:
            if not isinstance(handle, AuthorizedResourceHandle) or handle.lease_id != lease_id or handle.operation_id != operation_id:
                return False
            binding = self._bindings.get(handle.handle_ref)
            lease = self._leases.get(lease_id)
            if binding is None or lease is None or lease.state is not ResourceLeaseState.LEASED or self.now() >= handle.expires_at:
                return False
            same_scope = all(getattr(context, field, None) == getattr(lease.context, field, None) for field in ("user_id", "workspace_id", "agent_id", "execution_id", "correlation_id", "purpose", "actor"))
            if not same_scope:
                return False
            if self.workspace_manager is not None and lease.workspace_lease_id is not None:
                workspace_lease = self.workspace_manager._lease_for(self._workspace_context(ResourceOperationContext(context.user_id, context.workspace_id, context.agent_id, context.execution_id, context.correlation_id, context.purpose, context.actor)), lease.workspace_lease_id)
                if hasattr(workspace_lease, "code") or workspace_lease.state.value != "ACTIVE":
                    return False
            return any(cap.value.startswith("FILESYSTEM_") for cap in handle.capabilities)

    def sweep(self, *, cutoff_at: datetime, maximum: int = 100):
        with self._lock:
            results = []
            for lease in tuple(self._leases.values())[:maximum]:
                if lease.state is ResourceLeaseState.LEASED and lease.expires_at <= cutoff_at:
                    expired = replace(lease, state=ResourceLeaseState.EXPIRED)
                    self._leases[lease.lease_id] = expired
                    self._event("ResourceLeaseExpired", expired)
                    results.append(CleanupResult(lease.lease_id, "EXPIRED", EffectState.APPLIED))
            return tuple(results)

    def reconcile(self, *, resource_ref: str, context: ResourceOperationContext, maximum: int) -> ReconciliationResult:
        with self._lock:
            matching = [lease for lease in self._leases.values() if lease.resource_ref == resource_ref and same_binding(lease.context, context)]
            return ReconciliationResult(resource_ref, min(len(matching), maximum), 0, EffectState.APPLIED, evidence_codes=("LEASE_STATE_OBSERVED",))


__all__ = ["InMemoryResourceEventSink", "ResourceManagerService"]
