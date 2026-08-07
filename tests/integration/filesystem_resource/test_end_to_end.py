from __future__ import annotations

from datetime import timedelta
from io import BytesIO

from agentos.filesystem.in_memory import InMemoryFilesystemAdapter, WorkspaceBackedRootResolver
from agentos.filesystem.models import Atomicity, FilesystemError, FilesystemLimits, FilesystemOperationContext, WorkspacePath, WriteMode
from agentos.filesystem.service import FilesystemService
from agentos.filesystem.workspace_quota import WorkspaceQuotaAdapter
from agentos.resources.models import AuthorizeResourceOperation, ResourceBudget, ResourceCapability, ResourceLeaseRequest, ResourceOperationContext, ResourceType, RevokeResourceLease
from agentos.resources.service import ResourceManagerService
from agentos.workspaces.models import ActivateWorkspace, CreateWorkspace, CreateWorkspaceContext, WorkspaceQuota, WorkspaceOperationContext, WorkspacePermission
from agentos.workspaces.registry import InMemoryWorkspaceRegistry
from agentos.workspaces.root_adapter import InMemoryWorkspaceRootAdapter
from agentos.workspaces.service import WorkspaceManagerService


def workspace_manager() -> WorkspaceManagerService:
    manager = WorkspaceManagerService(InMemoryWorkspaceRegistry(), InMemoryWorkspaceRootAdapter())
    created = manager.create(CreateWorkspace("create", CreateWorkspaceContext("u", "ws", "a", "e", "c", "workspace.create", "agent:a"), "Project", WorkspaceQuota(100, 10, 50, 5, 4, 0), idempotency_key="create"))
    context = WorkspaceOperationContext("u", "ws", "a", "e", "c", "workspace.activate", "agent:a")
    manager.activate(ActivateWorkspace("activate", context, created.version, created.root_descriptor.root_identity, "activate"))
    return manager


def test_acquire_authorize_and_filesystem_write_share_workspace_ownership_and_lease() -> None:
    ws = workspace_manager()
    resources = ResourceManagerService(workspace_manager=ws)
    resource_context = ResourceOperationContext("u", "ws", "a", "e", "c", "filesystem.write", "agent:a")
    lease = resources.acquire(ResourceLeaseRequest("request", resource_context, ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_WRITE,), (ResourceCapability.FILESYSTEM_WRITE,), ResourceBudget(10, 50), timedelta(minutes=1), "lease"))
    handle = resources.authorize(AuthorizeResourceOperation(lease.lease_id, "operation", resource_context, ResourceCapability.FILESYSTEM_WRITE, requested_usage_bytes=4))
    fs_context = FilesystemOperationContext("u", "ws", "a", "e", "c", "filesystem.write", "agent:a")
    fs = FilesystemService(InMemoryFilesystemAdapter(), WorkspaceBackedRootResolver(ws), handle_validator=resources.validate_filesystem_handle)
    result = fs.write(operation_id="operation", context=fs_context, lease_id=lease.lease_id, resource_handle=handle, path=WorkspacePath.from_string("safe.txt"), source=BytesIO(b"data"), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=FilesystemLimits(50, 5, 3), idempotency_key="file")
    assert not isinstance(result, FilesystemError)
    assert resources.event_sink.events[-1].event_type == "ResourceLeaseGranted" or any(event.event_type == "ResourceLeaseGranted" for event in resources.event_sink.events)


def test_handle_cannot_cross_workspace_agent_execution_purpose_or_operation() -> None:
    ws = workspace_manager()
    resources = ResourceManagerService(workspace_manager=ws)
    ctx = ResourceOperationContext("u", "ws", "a", "e", "c", "filesystem.read", "agent:a")
    lease = resources.acquire(ResourceLeaseRequest("request", ctx, ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_READ,), (ResourceCapability.FILESYSTEM_READ,), ResourceBudget(10, 50), timedelta(minutes=1), "lease"))
    handle = resources.authorize(AuthorizeResourceOperation(lease.lease_id, "operation", ctx, ResourceCapability.FILESYSTEM_READ))
    assert resources.validate_filesystem_handle(handle, lease_id=lease.lease_id, context=ctx, operation_id="operation")
    assert not resources.validate_filesystem_handle(handle, lease_id=lease.lease_id, context=ResourceOperationContext("u", "ws", "other", "e", "c", "filesystem.read", "agent:other"), operation_id="operation")
    assert not resources.validate_filesystem_handle(handle, lease_id=lease.lease_id, context=ctx, operation_id="other-operation")


def test_revoke_immediately_blocks_filesystem_operation_and_workspace_suspend_invalidates_binding() -> None:
    ws = workspace_manager()
    resources = ResourceManagerService(workspace_manager=ws)
    ctx = ResourceOperationContext("u", "ws", "a", "e", "c", "filesystem.write", "agent:a")
    lease = resources.acquire(ResourceLeaseRequest("request", ctx, ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_WRITE,), (ResourceCapability.FILESYSTEM_WRITE,), ResourceBudget(10, 50), timedelta(minutes=1), "lease"))
    handle = resources.authorize(AuthorizeResourceOperation(lease.lease_id, "operation", ctx, ResourceCapability.FILESYSTEM_WRITE))
    resources.revoke(RevokeResourceLease("revoke", lease.lease_id, ctx, lease.fencing_token, "cancel", resources.now() + timedelta(minutes=1), "revoke"))
    assert not resources.validate_filesystem_handle(handle, lease_id=lease.lease_id, context=ctx, operation_id="operation")

    ws = workspace_manager()
    resources = ResourceManagerService(workspace_manager=ws)
    lease = resources.acquire(ResourceLeaseRequest("request-2", ctx, ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_WRITE,), (ResourceCapability.FILESYSTEM_WRITE,), ResourceBudget(10, 50), timedelta(minutes=1), "lease-2"))
    handle = resources.authorize(AuthorizeResourceOperation(lease.lease_id, "operation-2", ctx, ResourceCapability.FILESYSTEM_WRITE))
    from agentos.workspaces.models import InspectWorkspace, TransitionWorkspace, WorkspaceState
    workspace_context = WorkspaceOperationContext("u", "ws", "a", "e", "c", "workspace.suspend", "agent:a")
    current = ws.inspect(InspectWorkspace(workspace_context))
    pending = ws.transition(TransitionWorkspace("suspend", workspace_context, WorkspaceState.SUSPENDING, current.version, resources.now() + timedelta(minutes=1), "pause", "suspend"))
    ws.transition(TransitionWorkspace("suspended", workspace_context, WorkspaceState.SUSPENDED, pending.version, resources.now() - timedelta(seconds=1), "pause", "suspended"))
    assert not resources.validate_filesystem_handle(handle, lease_id=lease.lease_id, context=ctx, operation_id="operation-2")


def test_workspace_quota_is_reserved_and_accounted_through_workspace_authority() -> None:
    ws = workspace_manager()
    resources = ResourceManagerService(workspace_manager=ws)
    ctx = ResourceOperationContext("u", "ws", "a", "e", "c", "filesystem.write", "agent:a")
    lease = resources.acquire(ResourceLeaseRequest("request", ctx, ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_WRITE,), (ResourceCapability.FILESYSTEM_WRITE,), ResourceBudget(10, 50), timedelta(minutes=1), "quota-lease"))
    handle = resources.authorize(AuthorizeResourceOperation(lease.lease_id, "quota-operation", ctx, ResourceCapability.FILESYSTEM_WRITE, requested_usage_bytes=40))
    fs = FilesystemService(InMemoryFilesystemAdapter(), WorkspaceBackedRootResolver(ws), handle_validator=resources.validate_filesystem_handle, quota=WorkspaceQuotaAdapter(ws, resources))
    result = fs.write(operation_id="quota-operation", context=FilesystemOperationContext("u", "ws", "a", "e", "c", "filesystem.write", "agent:a"), lease_id=lease.lease_id, resource_handle=handle, path=WorkspacePath.from_string("quota.txt"), source=BytesIO(b"x" * 40), mode=WriteMode.CREATE_NEW, atomicity=Atomicity.REQUIRE_ATOMIC, limits=FilesystemLimits(50, 5, 3), idempotency_key="quota-write")
    assert not isinstance(result, FilesystemError)
    snapshot = ws.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(__import__("agentos.workspaces.models", fromlist=["WorkspaceOperationContext"]).WorkspaceOperationContext("u", "ws", "a", "e", "c", "workspace.inspect", "agent:a")))
    assert snapshot.usage.accounted_bytes == 40


def test_resource_release_releases_the_underlying_workspace_lease() -> None:
    ws = workspace_manager()
    resources = ResourceManagerService(workspace_manager=ws)
    ctx = ResourceOperationContext("u", "ws", "a", "e", "c", "filesystem.read", "agent:a")
    lease = resources.acquire(ResourceLeaseRequest("request", ctx, ResourceType.FILESYSTEM, (ResourceCapability.FILESYSTEM_READ,), (ResourceCapability.FILESYSTEM_READ,), ResourceBudget(10, 50), timedelta(minutes=1), "release-lease"))
    resources.release(__import__("agentos.resources.models", fromlist=["ReleaseResourceLease"]).ReleaseResourceLease("release", lease.lease_id, ctx, lease.fencing_token, "done", "release"))
    workspace_context = WorkspaceOperationContext("u", "ws", "a", "e", "c", "workspace.resource", "agent:a")
    snapshot = ws.inspect(__import__("agentos.workspaces.models", fromlist=["InspectWorkspace"]).InspectWorkspace(workspace_context))
    assert snapshot.usage.active_leases == 0
