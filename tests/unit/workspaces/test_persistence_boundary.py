from __future__ import annotations

from agentos.events.models import DataClassification
from agentos.persistence import InMemoryTransactionalPersistence
from agentos.workspaces.models import (
    ActivateWorkspace,
    CreateWorkspace,
    CreateWorkspaceContext,
    WorkspaceOperationContext,
    WorkspaceQuota,
)
from agentos.workspaces.persistence import TransactionalWorkspaceRegistry
from agentos.workspaces.registry import InMemoryWorkspaceRegistry
from agentos.workspaces.root_adapter import InMemoryWorkspaceRootAdapter
from agentos.workspaces.service import WorkspaceManagerService
from agentos.workspaces.models import DeleteWorkspace, InspectWorkspace
from datetime import timedelta


def test_workspace_registry_round_trips_metadata_and_outbox_through_rfc601() -> None:
    persistence = InMemoryTransactionalPersistence()
    registry = TransactionalWorkspaceRegistry(persistence)
    service = WorkspaceManagerService(registry, InMemoryWorkspaceRootAdapter())
    created = service.create(CreateWorkspace("create", CreateWorkspaceContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.create", "user:user-1"), "Project", WorkspaceQuota(1000, 10, 500, 4, 2, 100), classification=DataClassification.CONFIDENTIAL, idempotency_key="create"))
    assert created.workspace_id == "ws-1"
    context = WorkspaceOperationContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.activate", "user:user-1")
    activated = service.activate(ActivateWorkspace("activate", context, created.version, created.root_descriptor.root_identity, "activate"))
    assert activated.state.value == "ACTIVE"
    record = registry.get(context)
    assert record.state.value == "ACTIVE"
    assert any(event.event_type == "WorkspaceActivated" for event in (item.event for item in persistence.confirmed_outbox()))


def test_creation_idempotency_and_allocated_id_survive_registry_restart() -> None:
    persistence = InMemoryTransactionalPersistence()
    adapter = InMemoryWorkspaceRootAdapter()
    command = CreateWorkspace("create", CreateWorkspaceContext("user-1", None, "agent-1", "exec-1", "corr-1", "workspace.create", "user:user-1"), "Project", WorkspaceQuota(1000, 10, 500, 4, 2, 100), idempotency_key="stable-create")
    first = WorkspaceManagerService(TransactionalWorkspaceRegistry(persistence), adapter).create(command)
    restarted = WorkspaceManagerService(TransactionalWorkspaceRegistry(persistence), adapter)
    restarted._counter = 500
    second = restarted.create(command)
    assert second.workspace_id == first.workspace_id
    assert len(persistence.confirmed_outbox()) == 1


def test_delete_fence_and_checkpoint_survive_registry_restart() -> None:
    persistence = InMemoryTransactionalPersistence()
    adapter = InMemoryWorkspaceRootAdapter()
    service = WorkspaceManagerService(TransactionalWorkspaceRegistry(persistence), adapter)
    created = service.create(CreateWorkspace("create", CreateWorkspaceContext("user-1", "ws-delete", "agent-1", "exec-1", "corr-1", "workspace.create", "user:user-1"), "Project", WorkspaceQuota(1000, 100, 500, 4, 2, 100), idempotency_key="delete-create"))
    context = WorkspaceOperationContext("user-1", "ws-delete", "agent-1", "exec-1", "corr-1", "workspace.activate", "user:user-1")
    active = service.activate(ActivateWorkspace("activate", context, created.version, created.root_descriptor.root_identity, "delete-activate"))
    adapter.seed_entries("ws-delete", entries=40, bytes_count=400)
    first = service.delete(DeleteWorkspace("delete", context, active.version, active.root_descriptor.root_identity, timedelta(minutes=5), "cleanup", "delete-1"))
    assert first.checkpoint is not None
    restarted = WorkspaceManagerService(TransactionalWorkspaceRegistry(persistence), adapter)
    current = restarted.inspect(InspectWorkspace(context))
    second = restarted.delete(DeleteWorkspace("delete-retry", context, current.version, current.root_descriptor.root_identity, timedelta(minutes=5), "cleanup", "delete-2"))
    assert second.state.value == "DELETED"
    assert second.fence == first.fence


def test_transactional_registry_persists_quota_rejection_fact_to_outbox() -> None:
    from agentos.workspaces.models import AcquireWorkspaceLease, ReserveWorkspaceUsage, WorkspaceOperationBudget, WorkspacePermission
    persistence = InMemoryTransactionalPersistence()
    adapter = InMemoryWorkspaceRootAdapter()
    service = WorkspaceManagerService(TransactionalWorkspaceRegistry(persistence), adapter)
    created = service.create(CreateWorkspace("create", CreateWorkspaceContext("user-1", "ws-quota", "agent-1", "exec-1", "corr-1", "workspace.create", "user:user-1"), "Project", WorkspaceQuota(10, 2, 5, 1, 1, 1), idempotency_key="quota-create"))
    context = WorkspaceOperationContext("user-1", "ws-quota", "agent-1", "exec-1", "corr-1", "workspace.activate", "user:user-1")
    active = service.activate(ActivateWorkspace("activate", context, created.version, created.root_descriptor.root_identity, "quota-activate"))
    lease = service.acquire_lease(AcquireWorkspaceLease("lease", context, (WorkspacePermission.WRITE,), timedelta(minutes=1), WorkspaceOperationBudget(100, 2, 1), active.version, active.root_descriptor.root_identity, "quota-lease"))
    rejected = service.reserve_usage(ReserveWorkspaceUsage("reserve", context, lease.lease_id, lease.fencing_token, 11, 1, 5, 1, service.inspect(InspectWorkspace(context)).version, "quota-reserve"))
    assert rejected.code.value == "QUOTA_EXCEEDED"
    assert any(item.event.event_type == "WorkspaceQuotaExceeded" for item in persistence.confirmed_outbox())
