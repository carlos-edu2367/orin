from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from agentos.resources.models import ResourceCapability, ResourceError, ResourceLeaseRequest, ResourceOperationContext, ResourceType, ResourceBudget
from agentos.resources.service import ResourceManagerService


def test_concurrent_idempotent_acquire_returns_one_lease() -> None:
    manager = ResourceManagerService()
    ctx = ResourceOperationContext("u", "ws", "a", "e", "c", "purpose", "agent:a")
    def acquire(index):
        return manager.acquire(ResourceLeaseRequest("request", ctx, ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_READ,), (ResourceCapability.FILESYSTEM_READ,), ResourceBudget(5, 10), timedelta(minutes=1), "same"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(acquire, range(8)))
    assert len({result.lease_id for result in results if not isinstance(result, ResourceError)}) == 1


def test_distinct_allocations_have_distinct_derived_isolation_keys() -> None:
    manager = ResourceManagerService()
    first = manager.acquire(ResourceLeaseRequest("one", ResourceOperationContext("u", "ws", "a", "e", "c", "purpose", "agent:a"), ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_READ,), (ResourceCapability.FILESYSTEM_READ,), ResourceBudget(5, 10), timedelta(minutes=1), "one"))
    second = manager.acquire(ResourceLeaseRequest("two", ResourceOperationContext("u", "ws", "a", "e-2", "c-2", "purpose", "agent:a"), ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_READ,), (ResourceCapability.FILESYSTEM_READ,), ResourceBudget(5, 10), timedelta(minutes=1), "two"))
    assert first.isolation_key != second.isolation_key
