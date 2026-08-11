from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentos.workspaces.models import (
    ActivateWorkspace,
    CreateWorkspace,
    CreateWorkspaceContext,
    DeleteWorkspace,
    ReconcileScope,
    ReconcileWorkspace,
    WorkspaceError,
    WorkspaceOperationContext,
    WorkspaceQuota,
    WorkspaceState,
)
from agentos.workspaces.registry import InMemoryWorkspaceRegistry
from agentos.workspaces.root_adapter import InMemoryWorkspaceRootAdapter
from agentos.workspaces.service import WorkspaceManagerService


def setup():
    adapter = InMemoryWorkspaceRootAdapter()
    service = WorkspaceManagerService(InMemoryWorkspaceRegistry(), adapter)
    created = service.create(CreateWorkspace("create", CreateWorkspaceContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.create", "user:user-1"), "Project", WorkspaceQuota(1000, 100, 500, 4, 2, 100), idempotency_key="create"))
    context = WorkspaceOperationContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.activate", "user:user-1")
    active = service.activate(ActivateWorkspace("activate", context, created.version, created.root_descriptor.root_identity, "activate"))
    return service, adapter, active, context


def delete_command(snapshot, context, *, key="delete", expected=None):
    return DeleteWorkspace("delete-op", context, expected or snapshot.version, snapshot.root_descriptor.root_identity, timedelta(minutes=5), "user requested deletion", key)


def test_delete_is_bounded_idempotent_and_tombstones_only_expected_root() -> None:
    service, adapter, active, context = setup()
    adapter.seed_entries("ws-1", entries=2, bytes_count=20)
    receipt = service.delete(delete_command(active, context))
    assert receipt.state is WorkspaceState.DELETED
    assert receipt.effect_state.value == "APPLIED"
    assert service.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(context)).state is WorkspaceState.DELETED
    retry = service.delete(delete_command(active, context))
    assert retry.state is WorkspaceState.DELETED
    assert service.reconcile(ReconcileWorkspace("reconcile", context, receipt.version, ReconcileScope.ROOT, 10, "reconcile")).state is WorkspaceState.DELETED


def test_partial_cleanup_keeps_deleting_and_never_returns_active() -> None:
    service, adapter, active, context = setup()
    adapter.seed_entries("ws-1", entries=50, bytes_count=500)
    receipt = service.delete(delete_command(active, context))
    assert receipt.state is WorkspaceState.DELETING
    assert receipt.checkpoint is not None
    current = service.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(context))
    completed = service.delete(delete_command(current, context, key="delete-retry", expected=current.version))
    assert completed.state is WorkspaceState.DELETED
    assert completed.state is not WorkspaceState.ACTIVE


def test_root_swap_or_missing_during_cleanup_requires_recovery_without_expanding_target() -> None:
    service, adapter, active, context = setup()
    adapter.seed_entries("ws-1", entries=2, bytes_count=20)
    adapter.swap_identity("ws-1")
    receipt = service.delete(delete_command(active, context))
    assert receipt.state is WorkspaceState.RECOVERY_REQUIRED
    assert receipt.effect_state.value == "UNKNOWN"
    current = service.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(context))
    assert current.state is WorkspaceState.RECOVERY_REQUIRED


def test_reconcile_usage_marks_divergence_and_root_absence_is_fail_closed() -> None:
    service, adapter, active, context = setup()
    adapter.seed_entries("ws-1", entries=3, bytes_count=30)
    usage = service.reconcile(ReconcileWorkspace("usage", context, active.version, ReconcileScope.USAGE, 10, "usage"))
    assert usage.effect_state.value == "APPLIED"
    adapter.remove_root("ws-1")
    current = service.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(context))
    root = service.reconcile(ReconcileWorkspace("root", context, current.version, ReconcileScope.ROOT, 10, "root"))
    assert root.state is WorkspaceState.RECOVERY_REQUIRED
    assert root.effect_state.value == "APPLIED"

