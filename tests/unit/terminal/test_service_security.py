from __future__ import annotations

from datetime import timedelta

from agentos.filesystem.models import WorkspacePath
from agentos.resources.models import ResourceBudget, ResourceCapability, ResourceLeaseRequest, ResourceOperationContext, ResourceType
from agentos.resources.service import ResourceManagerService
from agentos.terminal.models import AuthorizedTerminalQuery, CreateTerminalSession, TerminalError, TerminalLimits, TerminalOperationContext
from agentos.terminal.reference import ReferenceTerminalAdapter
from agentos.terminal.service import InMemoryTerminalEventSink, TerminalService


def ctx(agent: str = "agent-1") -> TerminalOperationContext:
    return TerminalOperationContext("u", "ws", agent, "e", "c", "terminal.session", f"agent:{agent}")


def limits() -> TerminalLimits:
    return TerminalLimits(timedelta(minutes=5), timedelta(seconds=5), 1, 1024, timedelta(seconds=5), 32, 32, 32, "network:denied")


def make_lease(manager: ResourceManagerService, context: TerminalOperationContext):
    resource_context = ResourceOperationContext(*context.scope_key())
    return manager.acquire(ResourceLeaseRequest("request-1", resource_context, ResourceType.TERMINAL, (ResourceCapability.TERMINAL_SESSION,), (ResourceCapability.TERMINAL_SESSION, ResourceCapability.INSPECT), ResourceBudget(10, 100), timedelta(minutes=5), "lease-1"))


def test_foreign_binding_cannot_inspect_or_reuse_session() -> None:
    manager = ResourceManagerService()
    original = ctx()
    lease = make_lease(manager, original)
    service = TerminalService(resource_manager=manager, adapter=ReferenceTerminalAdapter())
    created = service.create(CreateTerminalSession("create", original, lease.lease_id, WorkspacePath.root(), "shell:reference", ("secret-ref:one",), limits(), "create"))
    denied = service.inspect(AuthorizedTerminalQuery(ctx("agent-2"), lease.lease_id, created.id))
    assert isinstance(denied, TerminalError)


def test_events_and_errors_do_not_leak_command_output_or_secret_reference() -> None:
    manager = ResourceManagerService()
    original = ctx()
    lease = make_lease(manager, original)
    sink = InMemoryTerminalEventSink()
    service = TerminalService(resource_manager=manager, adapter=ReferenceTerminalAdapter(), event_sink=sink)
    created = service.create(CreateTerminalSession("create", original, lease.lease_id, WorkspacePath.root(), "shell:reference", ("secret-ref:one",), limits(), "create"))
    assert created.id
    text = "echo TOP-SECRET"
    assert text not in repr(service.event_sink.events)
    assert "secret-ref:one" not in repr(service.event_sink.events)
    assert "TOP-SECRET" not in repr(TerminalError("UNAUTHORIZED"))
