from __future__ import annotations

from datetime import timedelta

from agentos.resources.models import ResourceCapability, ResourceLeaseRequest, ResourceOperationContext, ResourceType, ResourceBudget, ResourceError, ResourceErrorCode, RevokeResourceLease
from agentos.resources.service import ResourceManagerService


def test_revoke_signals_cancel_and_cleanup_and_late_handle_is_blocked() -> None:
    manager = ResourceManagerService()
    ctx = ResourceOperationContext("u", "ws", "a", "e", "c", "purpose", "agent:a")
    lease = manager.acquire(ResourceLeaseRequest("one", ctx, ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_READ,), (ResourceCapability.FILESYSTEM_READ,), ResourceBudget(5, 10), timedelta(minutes=1), "one"))
    handle = manager.authorize(__import__("agentos.resources.models", fromlist=["AuthorizeResourceOperation"]).AuthorizeResourceOperation(lease.lease_id, "op", ctx, ResourceCapability.FILESYSTEM_READ))
    result = manager.revoke(RevokeResourceLease("revoke", lease.lease_id, ctx, lease.fencing_token, "cancel", manager.now() + timedelta(minutes=1), "revoke"))
    assert result.state == "REVOKED"
    late = manager.validate_filesystem_handle(handle, lease_id=lease.lease_id, context=ctx, operation_id="late")
    assert late is False


def test_cleanup_failure_quarantines_adapter_and_reports_failure() -> None:
    manager = ResourceManagerService()
    manager.fail_cleanup_next(ResourceType.FILESYSTEM)
    ctx = ResourceOperationContext("u", "ws", "a", "e", "c", "purpose", "agent:a")
    lease = manager.acquire(ResourceLeaseRequest("one", ctx, ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_READ,), (ResourceCapability.FILESYSTEM_READ,), ResourceBudget(5, 10), timedelta(minutes=1), "one"))
    result = manager.revoke(RevokeResourceLease("revoke", lease.lease_id, ctx, lease.fencing_token, "cancel", manager.now() + timedelta(minutes=1), "revoke"))
    assert isinstance(result, ResourceError) and result.code is ResourceErrorCode.CLEANUP_FAILED
    assert manager.descriptor(ResourceType.FILESYSTEM).health.value == "QUARANTINED"
    assert manager.event_sink.events[-1].event_type == "ResourceCleanupFailed"


def test_filesystem_resource_cleanup_calls_the_filesystem_port() -> None:
    class SpyFilesystem:
        def __init__(self):
            self.leases = []

        def cleanup_lease(self, lease_id: str) -> bool:
            self.leases.append(lease_id)
            return True

    manager = ResourceManagerService()
    spy = SpyFilesystem()
    manager.bind_filesystem_port(spy)
    ctx = ResourceOperationContext("u", "ws", "a", "e", "c", "purpose", "agent:a")
    lease = manager.acquire(ResourceLeaseRequest("one", ctx, ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_READ,), (ResourceCapability.FILESYSTEM_READ,), ResourceBudget(5, 10), timedelta(minutes=1), "one"))
    result = manager.release(__import__("agentos.resources.models", fromlist=["ReleaseResourceLease"]).ReleaseResourceLease("release", lease.lease_id, ctx, lease.fencing_token, "done", "release"))
    assert result.effect_state.value == "APPLIED"
    assert spy.leases == [lease.lease_id]
