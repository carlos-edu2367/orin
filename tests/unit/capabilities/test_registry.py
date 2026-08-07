from dataclasses import replace

import pytest

from agentos.capabilities.models import (
    CapabilityOperationContext,
    CapabilityDescriptor,
    CapabilityProgram,
    CapabilityRef,
    CapabilityRegistryOperationContext,
    CapabilityStatus,
    DisableCapability,
    RegisterCapability,
)
from agentos.capabilities.registry import (
    InMemoryCapabilityRegistry,
    RegistryConflict,
    RegistryNotFound,
)
from .test_contracts import descriptor


def admin_context(**changes):
    value = CapabilityRegistryOperationContext(
        user_id="user:1",
        workspace_id="workspace:1",
        agent_id=None,
        execution_id=None,
        administrative_correlation_id="admin:1",
        correlation_id="correlation:1",
        purpose="catalog.maintain",
        actor="actor:1",
    )
    return replace(value, **changes)


def register_request(key="register:1", *, context=None, version=1):
    return RegisterCapability(
        request_id="request:1",
        context=context or admin_context(),
        descriptor=replace(descriptor(), capability_ref=CapabilityRef("capability:test", version)),
        program=CapabilityProgram(steps=(), compensation_steps=()),
        package_integrity_ref="integrity:1",
        idempotency_key=key,
    )


def execution_context(**changes):
    value = CapabilityOperationContext(
        user_id="user:1",
        workspace_id="workspace:1",
        agent_id="agent:1",
        execution_id="execution:1",
        correlation_id="correlation:1",
        purpose="capability.test",
        actor="actor:1",
    )
    return replace(value, **changes)


def test_registry_keeps_published_versions_immutable_and_resolves_exact_version():
    registry = InMemoryCapabilityRegistry()
    registry.register(register_request())
    with pytest.raises(RegistryConflict):
        registry.register(register_request(key="register:2"))
    resolved = registry.resolve(CapabilityRef("capability:test", 1), execution_context())
    assert resolved.status is CapabilityStatus.ACTIVE
    assert len(registry.list(admin_context())) == 1


def test_bootstrap_is_allowlisted_once_and_known_id_does_not_authorize():
    registry = InMemoryCapabilityRegistry()
    bootstrap = replace(admin_context(), purpose="SYSTEM_BOOTSTRAP")
    registry.register(register_request(context=bootstrap))
    with pytest.raises(PermissionError):
        registry.register(register_request(key="register:2", context=bootstrap, version=2))
    with pytest.raises(PermissionError):
        registry.resolve(CapabilityRef("capability:test", 1), replace(execution_context(), user_id="other"))


def test_disable_requires_expected_status_and_is_idempotent():
    registry = InMemoryCapabilityRegistry()
    registry.register(register_request())
    result = registry.disable(
        DisableCapability(
            request_id="disable:1",
            context=admin_context(),
            capability_ref=CapabilityRef("capability:test", 1),
            expected_status=CapabilityStatus.ACTIVE,
            reason="retired",
            idempotency_key="disable-key",
        )
    )
    assert result.descriptor.status is CapabilityStatus.DISABLED
    assert registry.resolve(CapabilityRef("capability:test", 1), execution_context()).status is CapabilityStatus.DISABLED
