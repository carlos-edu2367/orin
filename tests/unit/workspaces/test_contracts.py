from __future__ import annotations

from datetime import timedelta
import pickle

import pytest

from agentos.events.models import DataClassification
from agentos.workspaces.models import (
    CreateWorkspaceContext,
    EffectState,
    FilesystemObjectIdentity,
    FencingToken,
    OpaqueRootHandleRef,
    OpaqueWorkspaceRootRef,
    WorkspaceOperationBudget,
    WorkspaceOperationContext,
    WorkspacePermission,
    WorkspaceQuota,
    WorkspaceRootDescriptor,
    WorkspaceState,
    WorkspaceUsage,
    UsageReconciliationState,
)


def ctx(workspace_id: str | None = "ws-1") -> WorkspaceOperationContext:
    assert workspace_id is not None
    return WorkspaceOperationContext("user-1", workspace_id, "agent-1", "exec-1", "corr-1", "workspace.read", "user:user-1")


def test_workspace_operation_context_requires_complete_binding_and_hides_purpose() -> None:
    value = ctx()
    assert value.scope_key() == ("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.read", "user:user-1")
    assert "workspace.read" not in repr(value)
    with pytest.raises(ValueError):
        WorkspaceOperationContext("user-1", "ws-1", "agent-1", "", "corr-1", "workspace.read", "user:user-1")


def test_create_context_is_the_only_bootstrap_exception() -> None:
    value = CreateWorkspaceContext("user-1", "requested-1", "agent-1", "exec-1", "corr-1", "workspace.create", "user:user-1")
    assert value.requested_workspace_id == "requested-1"
    assert value.workspace_id is None
    with pytest.raises(ValueError):
        CreateWorkspaceContext("user-1", "../escape", "agent-1", "exec-1", "corr-1", "workspace.create", "user:user-1")


def test_states_and_quotas_are_exact_and_bounded() -> None:
    assert {state.value for state in WorkspaceState} == {
        "PROVISIONING", "ACTIVE", "SUSPENDING", "SUSPENDED", "ARCHIVING",
        "ARCHIVED", "DELETING", "DELETED", "RECOVERY_REQUIRED", "FAILED",
    }
    quota = WorkspaceQuota(1000, 10, 500, 4, 2, 100)
    assert quota.maximum_bytes == 1000
    assert WorkspaceUsage(0, 0, 0, 0, WorkspaceUsage.now(), UsageReconciliationState.CURRENT).reconciliation_state is UsageReconciliationState.CURRENT
    with pytest.raises(ValueError):
        WorkspaceQuota(1, 1, 2, 1, 1, 0)
    with pytest.raises(ValueError):
        WorkspaceOperationBudget(maximum_bytes=1, maximum_entries=1, maximum_depth=1, duration=timedelta(seconds=0))


def test_opaque_root_refs_and_handles_are_not_serializable_or_physical() -> None:
    root_ref = OpaqueWorkspaceRootRef("root-internal-1")
    identity = FilesystemObjectIdentity("identity-internal-1")
    handle = OpaqueRootHandleRef("handle-internal-1")
    assert repr(root_ref) == "OpaqueWorkspaceRootRef(<opaque>)"
    assert repr(identity) == "FilesystemObjectIdentity(<opaque>)"
    assert repr(handle) == "OpaqueRootHandleRef(<ephemeral>)"
    assert "internal" not in repr(root_ref) + repr(identity) + repr(handle)
    with pytest.raises(TypeError):
        pickle.dumps(handle)


def test_root_descriptor_contains_only_opaque_identity() -> None:
    descriptor = WorkspaceRootDescriptor(
        workspace_id="ws-1",
        root_ref=OpaqueWorkspaceRootRef("root-1"),
        root_identity=FilesystemObjectIdentity("identity-1"),
        storage_class="LOCAL_REFERENCE",
        containment_policy_version=1,
        provisioned_at=WorkspaceUsage.now(),
        health="READY",
    )
    assert descriptor.workspace_id == "ws-1"
    assert "root-1" not in repr(descriptor)


def test_permissions_and_effect_states_are_publicly_bounded() -> None:
    assert WorkspacePermission.READ.value == "READ"
    assert EffectState.UNKNOWN.value == "UNKNOWN"
    with pytest.raises(ValueError):
        WorkspacePermission("ROOT_ADMIN")

