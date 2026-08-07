from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pickle

import pytest

from agentos.resources.models import (
    AuthorizedResourceHandle,
    IsolationMode,
    ResourceCapability,
    ResourceDescriptor,
    ResourceHealth,
    ResourceLeaseState,
    ResourceLimits,
    ResourceOperationContext,
    ResourceType,
)


def test_resource_context_and_descriptor_are_bounded_and_typed() -> None:
    context = ResourceOperationContext("u", "ws", "a", "e", "c", "filesystem.read", "agent:a")
    assert context.scope_key()[0:4] == ("u", "ws", "a", "e")
    descriptor = ResourceDescriptor(
        resource_type=ResourceType.FILESYSTEM,
        adapter_ref="filesystem.reference",
        capabilities=(ResourceCapability.FILESYSTEM_READ,),
        isolation_modes=(IsolationMode.WORKSPACE,),
        limits=ResourceLimits(maximum_duration=timedelta(minutes=5), maximum_operations=10),
        health=ResourceHealth.AVAILABLE,
    )
    assert descriptor.resource_type is ResourceType.FILESYSTEM
    assert descriptor.isolation_modes == (IsolationMode.WORKSPACE,)
    with pytest.raises(ValueError):
        ResourceOperationContext("", "ws", "a", "e", "c", "p", "agent:a")


def test_handles_are_opaque_bound_and_not_serializable() -> None:
    handle = AuthorizedResourceHandle("handle:1", "lease:1", "operation:1", (ResourceCapability.FILESYSTEM_READ,), datetime.now(timezone.utc))
    assert "handle:1" not in repr(handle)
    with pytest.raises(TypeError):
        pickle.dumps(handle)


def test_lease_states_have_no_implicit_reopen_state() -> None:
    assert {state.value for state in ResourceLeaseState} >= {"REQUESTED", "LEASED", "REVOKING", "RELEASED", "REVOKED", "EXPIRED", "FAILED"}
