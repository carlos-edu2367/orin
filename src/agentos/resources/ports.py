from __future__ import annotations

from typing import Protocol

from .models import (
    AuthorizedResourceHandle,
    CleanupResult,
    ResourceDescriptor,
    ResourceError,
    ResourceLease,
    ResourceLeaseRequest,
    ResourceOperationContext,
    ResourceType,
    ReconciliationResult,
    RenewResourceLease,
    AuthorizeResourceOperation,
    ReleaseResourceLease,
    RevokeResourceLease,
)


class AdapterResourceHandle:
    __slots__ = ("_value", "adapter_ref", "lease_id")

    def __init__(self, value: str, adapter_ref: str, lease_id: str) -> None:
        self._value = value
        self.adapter_ref = adapter_ref
        self.lease_id = lease_id

    def __repr__(self) -> str:
        return "AdapterResourceHandle(<internal>)"


class ResourceAdapter(Protocol):
    def allocate(self, *, lease_id: str, descriptor: ResourceDescriptor, context: ResourceOperationContext, isolation_key: str) -> AdapterResourceHandle | ResourceError: ...
    def inspect(self, handle: AdapterResourceHandle) -> str: ...
    def signal_cancel(self, handle: AdapterResourceHandle, reason: str) -> None: ...
    def cleanup(self, handle: AdapterResourceHandle, *, deadline) -> CleanupResult: ...


class ResourceCatalog(Protocol):
    def register(self, descriptor: ResourceDescriptor, adapter: ResourceAdapter) -> None: ...
    def descriptor(self, resource_type: ResourceType) -> ResourceDescriptor | None: ...
    def snapshot(self) -> tuple[ResourceDescriptor, ...]: ...


class ResourceManager(Protocol):
    def acquire(self, request: ResourceLeaseRequest) -> ResourceLease | ResourceError: ...
    def renew(self, request: RenewResourceLease) -> ResourceLease | ResourceError: ...
    def authorize(self, request: AuthorizeResourceOperation) -> AuthorizedResourceHandle | ResourceError: ...
    def release(self, request: ReleaseResourceLease) -> CleanupResult | ResourceError: ...
    def revoke(self, request: RevokeResourceLease) -> CleanupResult | ResourceError: ...
    def inspect(self, *, context: ResourceOperationContext, lease_id: str) -> ResourceLease | ResourceError: ...


class CleanupSupervisor(Protocol):
    def sweep(self, *, cutoff_at, maximum: int) -> tuple[CleanupResult, ...]: ...
    def reconcile(self, *, resource_ref: str, context: ResourceOperationContext, maximum: int) -> ReconciliationResult: ...


__all__ = [name for name in globals() if not name.startswith("_")]
