from __future__ import annotations

from datetime import timedelta
from datetime import datetime, timezone

from agentos.filesystem.models import WorkspacePath
from agentos.resources.models import ResourceBudget, ResourceCapability, ResourceLeaseRequest, ResourceOperationContext, ResourceType
from agentos.resources.service import ResourceManagerService
from agentos.terminal.models import (
    AuthorizedTerminalQuery,
    CreateTerminalSession,
    ExecuteTerminalCommand,
    TerminalCommand,
    TerminalError,
    TerminalLimits,
    TerminalOperationContext,
    TerminalSessionStatus,
)
from agentos.terminal.reference import ReferenceTerminalAdapter
from agentos.terminal.service import TerminalService


def context(*, agent: str = "agent-1", execution: str = "execution-1", purpose: str = "terminal.session") -> TerminalOperationContext:
    return TerminalOperationContext("user-1", "workspace-1", agent, execution, "correlation-1", purpose, f"agent:{agent}")


def limits() -> TerminalLimits:
    return TerminalLimits(timedelta(minutes=5), timedelta(seconds=30), 2, 1024, timedelta(seconds=30), 128, 128, 128, "network:denied")


def lease(manager: ResourceManagerService, ctx: TerminalOperationContext, key: str = "lease-key"):
    resource_context = ResourceOperationContext(*ctx.scope_key())
    return manager.acquire(ResourceLeaseRequest("request:" + key, resource_context, ResourceType.TERMINAL, (ResourceCapability.TERMINAL_SESSION,), (ResourceCapability.TERMINAL_SESSION, ResourceCapability.TERMINAL_CANCEL, ResourceCapability.INSPECT), ResourceBudget(10, 100), timedelta(minutes=5), key))


def create_request(ctx: TerminalOperationContext, lease_id: str) -> CreateTerminalSession:
    return CreateTerminalSession("create-1", ctx, lease_id, WorkspacePath.root(), "shell:reference", (), limits(), "create-key")


def command(ctx: TerminalOperationContext, session_id: str, key: str = "command-key") -> TerminalCommand:
    return TerminalCommand("command-1", session_id, ctx, "echo ok", WorkspacePath.root(), (), timedelta(seconds=5), 64, key)


def test_service_creates_and_executes_once_through_resource_manager() -> None:
    ctx = context()
    manager = ResourceManagerService()
    resource_lease = lease(manager, ctx)
    adapter = ReferenceTerminalAdapter()
    service = TerminalService(resource_manager=manager, adapter=adapter)
    created = service.create(create_request(ctx, resource_lease.lease_id))
    assert created.status is TerminalSessionStatus.READY
    accepted = service.execute(ExecuteTerminalCommand(command(ctx, created.id)))
    repeated = service.execute(ExecuteTerminalCommand(command(ctx, created.id)))
    assert accepted == repeated
    assert service.inspect(AuthorizedTerminalQuery(ctx, resource_lease.lease_id, created.id)).status is TerminalSessionStatus.READY


def test_service_rejects_binding_mismatch_and_expired_lease_before_adapter_effect() -> None:
    ctx = context()
    manager = ResourceManagerService()
    resource_lease = lease(manager, ctx)
    adapter = ReferenceTerminalAdapter()
    service = TerminalService(resource_manager=manager, adapter=adapter)
    created = service.create(create_request(ctx, resource_lease.lease_id))
    wrong = service.inspect(AuthorizedTerminalQuery(context(agent="agent-2", execution="execution-2"), resource_lease.lease_id, created.id))
    assert isinstance(wrong, TerminalError)
    manager._clock = lambda: resource_lease.expires_at + timedelta(seconds=1)
    expired = service.execute(ExecuteTerminalCommand(command(ctx, created.id, "after-expiry")))
    assert isinstance(expired, TerminalError)
    assert adapter.outcome("command-1") is None


def test_service_close_releases_resource_and_is_idempotent() -> None:
    ctx = context()
    manager = ResourceManagerService()
    resource_lease = lease(manager, ctx)
    service = TerminalService(resource_manager=manager, adapter=ReferenceTerminalAdapter())
    created = service.create(create_request(ctx, resource_lease.lease_id))
    close = __import__("agentos.terminal.models", fromlist=["CloseTerminalSession"]).CloseTerminalSession("close-1", ctx, resource_lease.lease_id, created.id, TerminalSessionStatus.READY, "done", resource_lease.expires_at, "close-key")
    first = service.close(close)
    second = service.close(close)
    assert first == second
    assert first.status is TerminalSessionStatus.CLOSED
    assert manager.inspect(context=ResourceOperationContext(*ctx.scope_key()), lease_id=resource_lease.lease_id).state.value == "RELEASED"


def test_close_cleanup_unknown_requires_recovery_and_never_claims_success() -> None:
    ctx = context()
    manager = ResourceManagerService()
    resource_lease = lease(manager, ctx)
    service = TerminalService(resource_manager=manager, adapter=ReferenceTerminalAdapter())
    created = service.create(create_request(ctx, resource_lease.lease_id))
    manager.fail_cleanup_next(__import__("agentos.resources.models", fromlist=["ResourceType"]).ResourceType.TERMINAL)
    close = __import__("agentos.terminal.models", fromlist=["CloseTerminalSession"]).CloseTerminalSession("close-unknown", ctx, resource_lease.lease_id, created.id, TerminalSessionStatus.READY, "done", resource_lease.expires_at, "close-unknown")
    result = service.close(close)
    assert result.status is TerminalSessionStatus.RECOVERY_REQUIRED
    assert result.effect_state.value == "UNKNOWN"
    assert result.lease_released is False


def test_reconcile_enforces_command_timeout_without_reexecution() -> None:
    class Clock:
        def __init__(self) -> None:
            self.value = datetime(2026, 8, 7, tzinfo=timezone.utc)

        def __call__(self):
            return self.value

    clock = Clock()
    ctx = context()
    manager = ResourceManagerService(clock=clock)
    resource_lease = lease(manager, ctx)
    adapter = ReferenceTerminalAdapter(clock=clock)
    service = TerminalService(resource_manager=manager, adapter=adapter, clock=clock)
    request = create_request(ctx, resource_lease.lease_id)
    created = service.create(request)
    adapter.register_result("hang", complete=False)
    command = TerminalCommand("timeout-command", created.id, ctx, "hang", WorkspacePath.root(), (), timedelta(seconds=1), 64, "timeout-command")
    service.execute(ExecuteTerminalCommand(command))
    clock.value += timedelta(seconds=2)
    reconciled = service.reconcile(created.id, ctx)
    assert reconciled.status is TerminalSessionStatus.READY
    assert adapter.outcome("timeout-command").termination_stage.value == "COOPERATIVE"
