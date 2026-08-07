from __future__ import annotations

from datetime import timedelta

from agentos.resources.models import ResourceCapability, ResourceBudget, ResourceLeaseRequest, ResourceOperationContext, ResourceType
from agentos.resources.service import ResourceManagerService


def test_resource_manager_never_uses_a_caller_supplied_isolation_key() -> None:
    manager = ResourceManagerService()
    context = ResourceOperationContext("u", "ws", "a", "e", "c", "purpose", "agent:a")
    request = ResourceLeaseRequest("request", context, ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_READ,), (ResourceCapability.FILESYSTEM_READ,), ResourceBudget(5, 10), timedelta(minutes=1), "key")
    lease = manager.acquire(request)
    assert lease.isolation_key != "caller-choice"
