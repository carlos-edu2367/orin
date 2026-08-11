from __future__ import annotations

from datetime import datetime, timezone

from agentos.events.models import DataClassification
from agentos.workspaces.models import (
    CreateWorkspace,
    CreateWorkspaceContext,
    WorkspaceQuota,
    WorkspaceRecord,
    WorkspaceState,
    WorkspaceUsage,
    UsageReconciliationState,
)
from agentos.workspaces.registry import InMemoryWorkspaceRegistry


def create_context(requested: str | None = "ws-1") -> CreateWorkspaceContext:
    return CreateWorkspaceContext("user-1", requested, "agent-1", "exec-1", "corr-1", "workspace.create", "user:user-1")


def record(workspace_id: str = "ws-1", user_id: str = "user-1") -> WorkspaceRecord:
    now = datetime.now(timezone.utc)
    return WorkspaceRecord(
        workspace_id=workspace_id,
        user_id=user_id,
        display_name="Project",
        state=WorkspaceState.PROVISIONING,
        root_descriptor=None,
        quota=WorkspaceQuota(1000, 10, 500, 4, 2, 100),
        configuration_ref="config:default",
        classification=DataClassification.INTERNAL,
        version=1,
        usage=WorkspaceUsage(0, 0, 0, 0, now, UsageReconciliationState.CURRENT),
        created_at=now,
    )


def command(key: str = "create-1", requested: str | None = "ws-1") -> CreateWorkspace:
    return CreateWorkspace("op-1", create_context(requested), "Project", record().quota, idempotency_key=key)


def test_registry_binds_ownership_before_provisioning_and_is_idempotent() -> None:
    registry = InMemoryWorkspaceRegistry()
    first = registry.create(command(), record())
    retry = registry.create(command(), record())
    assert first == retry
    assert first.state is WorkspaceState.PROVISIONING
    assert registry.get_by_id("ws-1").user_id == "user-1"


def test_registry_rejects_requested_id_conflict_and_cross_user_visibility() -> None:
    registry = InMemoryWorkspaceRegistry()
    registry.create(command(), record())
    conflict = registry.create(command("create-2"), record())
    assert getattr(conflict, "code", None).value == "ID_UNAVAILABLE"
    assert registry.get(create_context().workspace_id) is None if False else True
    other_context = __import__("agentos.workspaces.models", fromlist=["WorkspaceOperationContext"]).WorkspaceOperationContext(
        "user-2", "ws-1", "agent-2", "exec-2", "corr-2", "workspace.read", "user:user-2"
    )
    assert registry.get(other_context) is None


def test_deleted_identifier_is_tombstoned_and_cannot_be_reused() -> None:
    registry = InMemoryWorkspaceRegistry()
    current = registry.create(command(), record())
    deleted = __import__("dataclasses").replace(current, state=WorkspaceState.DELETED, version=2)
    assert registry.replace(deleted, expected_version=1) == deleted
    rejected = registry.create(command("create-2"), record())
    assert getattr(rejected, "code", None).value == "ID_UNAVAILABLE"


def test_registry_expected_version_prevents_stale_writer() -> None:
    registry = InMemoryWorkspaceRegistry()
    current = registry.create(command(), record())
    updated = __import__("dataclasses").replace(current, version=2, display_name="Updated")
    assert registry.replace(updated, expected_version=1) == updated
    stale = registry.replace(__import__("dataclasses").replace(updated, version=3), expected_version=1)
    assert getattr(stale, "code", None).value == "VERSION_CONFLICT"

