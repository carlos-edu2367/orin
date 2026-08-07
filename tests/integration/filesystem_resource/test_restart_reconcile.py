from __future__ import annotations

from datetime import timedelta

from agentos.resources.models import ResourceCapability, ResourceBudget, ResourceLeaseRequest, ResourceOperationContext, ResourceType
from agentos.resources.service import ResourceManagerService


def test_reconcile_after_restart_style_reconstruction_is_bounded_and_idempotent() -> None:
    manager = ResourceManagerService()
    context = ResourceOperationContext("u", "ws", "a", "e", "c", "purpose", "agent:a")
    lease = manager.acquire(ResourceLeaseRequest("request", context, ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_READ,), (ResourceCapability.FILESYSTEM_READ,), ResourceBudget(5, 10), timedelta(minutes=1), "key"))
    first = manager.reconcile(resource_ref=lease.resource_ref, context=context, maximum=1)
    second = manager.reconcile(resource_ref=lease.resource_ref, context=context, maximum=1)
    assert first == second and first.inspected == 1
