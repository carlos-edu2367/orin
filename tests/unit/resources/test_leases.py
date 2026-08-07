from __future__ import annotations

from datetime import timedelta

from agentos.resources.models import (
    ResourceBudget,
    ResourceCapability,
    ResourceLeaseRequest,
    ResourceOperationContext,
    ResourceType,
)


def test_lease_request_carries_full_context_capability_budget_duration_and_idempotency() -> None:
    request = ResourceLeaseRequest(
        request_id="request:1",
        context=ResourceOperationContext("u", "ws", "a", "e", "c", "purpose", "agent:a"),
        resource_type=ResourceType.FILESYSTEM,
        required_capabilities=(ResourceCapability.FILESYSTEM_READ,),
        requested_permissions=(ResourceCapability.FILESYSTEM_READ,),
        requested_budget=ResourceBudget(maximum_operations=3, maximum_bytes=10),
        requested_duration=timedelta(minutes=1),
        idempotency_key="idem:1",
    )
    assert request.context.workspace_id == "ws"
    assert request.requested_budget.maximum_operations == 3
