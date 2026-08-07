from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentos.resources.adapters import BrowserResourceAdapter, TerminalResourceAdapter
from agentos.resources.models import (
    AuthorizeResourceOperation,
    AuthorizedResourceHandle,
    ResourceBudget,
    ResourceCapability,
    ResourceDescriptor,
    ResourceError,
    ResourceErrorCode,
    ResourceHealth,
    ResourceLeaseRequest,
    ResourceOperationContext,
    ResourceType,
    RevokeResourceLease,
)
from agentos.resources.service import ResourceManagerService


def context(*, purpose="filesystem.read", workspace="ws-1", agent="agent-1", execution="exec-1") -> ResourceOperationContext:
    return ResourceOperationContext("user-1", workspace, agent, execution, "corr-1", purpose, f"agent:{agent}")


def request(*, purpose="filesystem.read", caps=(ResourceCapability.FILESYSTEM_READ,), resource_type=ResourceType.FILESYSTEM, key="lease-1", workspace="ws-1") -> ResourceLeaseRequest:
    return ResourceLeaseRequest("request:" + key, context(purpose=purpose, workspace=workspace), resource_type, caps, caps, ResourceBudget(10, 100), timedelta(minutes=1), key)


def test_acquire_authorize_release_requires_valid_lease_and_capability() -> None:
    manager = ResourceManagerService()
    lease = manager.acquire(request())
    assert lease.state.value == "LEASED"
    authorized = manager.authorize(AuthorizeResourceOperation(lease.lease_id, "operation:1", context(), ResourceCapability.FILESYSTEM_READ))
    assert isinstance(authorized, AuthorizedResourceHandle)
    manager.release(__import__("agentos.resources.models", fromlist=["ReleaseResourceLease"]).ReleaseResourceLease("release", lease.lease_id, context(), lease.fencing_token, "done", "release"))
    late = manager.authorize(AuthorizeResourceOperation(lease.lease_id, "operation:2", context(), ResourceCapability.FILESYSTEM_READ))
    assert isinstance(late, ResourceError) and late.code is ResourceErrorCode.LEASE_RELEASED


def test_cross_binding_expired_and_capability_mismatch_are_rejected() -> None:
    manager = ResourceManagerService()
    lease = manager.acquire(request())
    wrong_context = manager.authorize(AuthorizeResourceOperation(lease.lease_id, "operation:1", context(agent="agent-2", execution="exec-2"), ResourceCapability.FILESYSTEM_READ))
    assert isinstance(wrong_context, ResourceError) and wrong_context.code is ResourceErrorCode.UNAUTHORIZED
    wrong_cap = manager.authorize(AuthorizeResourceOperation(lease.lease_id, "operation:2", context(), ResourceCapability.FILESYSTEM_WRITE))
    assert isinstance(wrong_cap, ResourceError) and wrong_cap.code is ResourceErrorCode.CAPABILITY_DENIED
    manager._clock = lambda: lease.expires_at + timedelta(seconds=1)
    expired = manager.authorize(AuthorizeResourceOperation(lease.lease_id, "operation:3", context(), ResourceCapability.FILESYSTEM_READ))
    assert isinstance(expired, ResourceError) and expired.code is ResourceErrorCode.LEASE_EXPIRED


def test_unavailable_descriptor_rejects_without_allocating_lease() -> None:
    manager = ResourceManagerService()
    manager.set_health(ResourceType.FILESYSTEM, ResourceHealth.UNAVAILABLE)
    result = manager.acquire(request(key="unavailable"))
    assert isinstance(result, ResourceError) and result.code is ResourceErrorCode.HEALTH_REJECTED
    assert manager.active_leases() == ()


def test_reusing_acquire_idempotency_key_with_different_capability_is_rejected() -> None:
    manager = ResourceManagerService()
    first = manager.acquire(request(key="same", caps=(ResourceCapability.FILESYSTEM_READ,)))
    conflict = manager.acquire(request(key="same", caps=(ResourceCapability.FILESYSTEM_WRITE,)))
    assert first.lease_id
    assert isinstance(conflict, ResourceError) and conflict.code is ResourceErrorCode.INVALID_REQUEST


def test_authorize_same_operation_is_idempotent_and_does_not_double_count_budget() -> None:
    manager = ResourceManagerService()
    lease = manager.acquire(request())
    first = manager.authorize(AuthorizeResourceOperation(lease.lease_id, "operation:repeat", context(), ResourceCapability.FILESYSTEM_READ, requested_usage_operations=1))
    second = manager.authorize(AuthorizeResourceOperation(lease.lease_id, "operation:repeat", context(), ResourceCapability.FILESYSTEM_READ, requested_usage_operations=1))
    assert first == second
    assert manager.inspect(context=context(), lease_id=lease.lease_id).usage_operations == 1


def test_terminal_and_browser_reference_adapters_have_lifecycle_and_cleanup() -> None:
    manager = ResourceManagerService()
    terminal = manager.acquire(request(resource_type=ResourceType.TERMINAL, caps=(ResourceCapability.TERMINAL_SESSION,), key="terminal"))
    browser = manager.acquire(request(resource_type=ResourceType.BROWSER, caps=(ResourceCapability.BROWSER_SESSION,), key="browser"))
    assert terminal.resource_type is ResourceType.TERMINAL
    assert browser.resource_type is ResourceType.BROWSER
    assert manager.reconcile(resource_ref=terminal.resource_ref, context=terminal.context, maximum=10).inspected == 1
