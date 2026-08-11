from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentos.workspaces.models import (
    AcquireWorkspaceLease,
    AcquireWorkspaceLock,
    ActivateWorkspace,
    CreateWorkspace,
    CreateWorkspaceContext,
    FencingToken,
    WorkspaceError,
    WorkspaceOperationBudget,
    WorkspaceOperationContext,
    WorkspacePermission,
    WorkspaceQuota,
    WorkspaceState,
    RenewWorkspaceLease,
    InspectWorkspace,
    TransitionWorkspace,
)
from agentos.workspaces.registry import InMemoryWorkspaceRegistry
from agentos.workspaces.root_adapter import InMemoryWorkspaceRootAdapter
from agentos.workspaces.service import WorkspaceManagerService


def make_service() -> WorkspaceManagerService:
    service = WorkspaceManagerService(InMemoryWorkspaceRegistry(), InMemoryWorkspaceRootAdapter())
    created = service.create(CreateWorkspace("create", CreateWorkspaceContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.create", "user:user-1"), "Project", WorkspaceQuota(1000, 10, 500, 4, 2, 100), idempotency_key="create"))
    context = WorkspaceOperationContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.activate", "user:user-1")
    service.activate(ActivateWorkspace("activate", context, created.version, created.root_descriptor.root_identity, "activate"))
    return service


def lease_request(service: WorkspaceManagerService, *, key: str = "lease", context: WorkspaceOperationContext | None = None) -> AcquireWorkspaceLease:
    snapshot = service.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(context or WorkspaceOperationContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.read", "user:user-1")))
    return AcquireWorkspaceLease("lease-op", context or WorkspaceOperationContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.lease", "user:user-1"), (WorkspacePermission.READ, WorkspacePermission.WRITE), timedelta(minutes=1), WorkspaceOperationBudget(100, 2, 2), snapshot.version, snapshot.root_descriptor.root_identity, key)


def test_acquire_lease_revalidates_state_root_and_active_lease_quota() -> None:
    service = make_service()
    first = service.acquire_lease(lease_request(service))
    assert first.state.value == "ACTIVE"
    second = service.acquire_lease(lease_request(service, key="lease-2"))
    third = service.acquire_lease(lease_request(service, key="lease-3"))
    assert isinstance(third, WorkspaceError)
    assert third.code.value == "QUOTA_EXCEEDED"
    service.release_lease(__import__("agentos.workspaces.models", fromlist=["ReleaseWorkspaceLease"]).ReleaseWorkspaceLease("release", first.context, first.lease_id, first.fencing_token, "done", "release"))
    archived = service.transition(__import__("agentos.workspaces.models", fromlist=["TransitionWorkspace"]).TransitionWorkspace("suspend", first.context, WorkspaceState.SUSPENDING, service.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(first.context)).version, datetime.now(timezone.utc) + timedelta(minutes=1), "test", "suspend"))
    assert archived.state is WorkspaceState.SUSPENDING


def test_lease_retry_is_idempotent_and_expired_or_transferred_binding_is_rejected() -> None:
    service = make_service()
    request = lease_request(service)
    first = service.acquire_lease(request)
    assert service.acquire_lease(request) == first
    other = WorkspaceOperationContext("user-1", "ws-1", "agent-2", "exec-2", "corr-2", "workspace.lease", "user:user-1")
    denied = service.renew_lease(__import__("agentos.workspaces.models", fromlist=["RenewWorkspaceLease"]).RenewWorkspaceLease("renew", other, first.lease_id, first.expires_at, timedelta(minutes=1), first.workspace_version, first.root_identity, first.fencing_token, "renew"))
    assert isinstance(denied, WorkspaceError)
    assert denied.code.value == "UNAUTHORIZED"


def test_administrative_lock_uses_monotonic_fencing_and_old_token_cannot_mutate() -> None:
    service = make_service()
    context = WorkspaceOperationContext("user-1", "ws-1", "agent-1", "exec-1", "corr-1", "workspace.admin", "user:user-1")
    first = service.acquire_lock(AcquireWorkspaceLock("lock-1", context, timedelta(minutes=1), service.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(context)).version, "lock-1"))
    second = service.acquire_lock(AcquireWorkspaceLock("lock-2", context, timedelta(minutes=1), first.workspace_version, "lock-2"))
    assert second.fencing_token > first.fencing_token
    rejected = service.assert_fence(context, first.fencing_token)
    assert isinstance(rejected, WorkspaceError)
    assert rejected.code.value == "FENCE_REJECTED"


def test_root_swap_between_resolution_and_final_revalidation_denies_lease() -> None:
    service = make_service()
    adapter = service.root_adapter
    adapter.on_resolve = lambda workspace_id: adapter.swap_identity(workspace_id)
    denied = service.acquire_lease(lease_request(service))
    assert isinstance(denied, WorkspaceError)
    assert denied.code.value == "ROOT_MISMATCH"


def test_suspension_drains_before_barrier_and_cannot_restore_after_forced_revoke() -> None:
    service = make_service()
    lease = service.acquire_lease(lease_request(service))
    context = lease.context
    current = service.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(context))
    from datetime import datetime, timezone
    from agentos.workspaces.models import TransitionWorkspace
    pending = service.transition(TransitionWorkspace("suspending", context, "SUSPENDING", current.version, datetime.now(timezone.utc) + timedelta(minutes=1), "pause", "suspending"))
    blocked = service.transition(TransitionWorkspace("suspended", context, "SUSPENDED", pending.version, datetime.now(timezone.utc) + timedelta(minutes=1), "pause", "suspended"))
    assert isinstance(blocked, WorkspaceError)
    forced = service.transition(TransitionWorkspace("suspended-forced", context, "SUSPENDED", pending.version, datetime.now(timezone.utc) - timedelta(seconds=1), "pause", "suspended-forced"))
    assert forced.state.value == "SUSPENDED"
    restored = service.transition(TransitionWorkspace("restore", context, "ACTIVE", forced.version, datetime.now(timezone.utc) + timedelta(minutes=1), "restore", "restore"))
    assert isinstance(restored, WorkspaceError)
    assert restored.code.value == "STATE_REJECTED"


def test_renew_revalidates_lease_eligibility_and_extension_bound() -> None:
    service = make_service()
    lease = service.acquire_lease(lease_request(service))
    current = service.inspect(InspectWorkspace(lease.context))
    pending = service.transition(TransitionWorkspace("suspending", lease.context, WorkspaceState.SUSPENDING, current.version, datetime.now(timezone.utc) + timedelta(minutes=1), "pause", "suspending"))
    rejected = service.renew_lease(RenewWorkspaceLease("renew-suspended", lease.context, lease.lease_id, lease.expires_at, timedelta(minutes=1), pending.version, pending.root_descriptor.root_identity, lease.fencing_token, "renew-suspended"))
    assert isinstance(rejected, WorkspaceError)
    assert rejected.code.value == "STATE_REJECTED"
    invalid = service.renew_lease(RenewWorkspaceLease("renew-too-long", lease.context, lease.lease_id, lease.expires_at, timedelta(hours=2), pending.version, pending.root_descriptor.root_identity, lease.fencing_token, "renew-too-long"))
    assert isinstance(invalid, WorkspaceError)
    assert invalid.code.value == "INVALID_REQUEST"
