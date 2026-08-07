from __future__ import annotations

from datetime import timedelta

from agentos.filesystem.models import WorkspacePath
from agentos.resources.models import ResourceBudget, ResourceCapability, ResourceLeaseRequest, ResourceOperationContext, ResourceType
from agentos.resources.service import ResourceManagerService
from agentos.terminal.models import (
    CreateTerminalSession,
    ExecuteTerminalCommand,
    InputWriteResult,
    StreamDisposition,
    StreamTerminalOutput,
    TerminalCommand,
    TerminalLimits,
    TerminalOperationContext,
    WriteTerminalInput,
)
from agentos.terminal.reference import ReferenceTerminalAdapter
from agentos.terminal.service import TerminalService


def ctx() -> TerminalOperationContext:
    return TerminalOperationContext("u", "ws", "a", "e", "c", "terminal.execute", "agent:a")


def make() -> tuple[TerminalService, ResourceManagerService, object, TerminalOperationContext]:
    context = ctx()
    manager = ResourceManagerService()
    resource_context = ResourceOperationContext(*context.scope_key())
    lease = manager.acquire(ResourceLeaseRequest("request", resource_context, ResourceType.TERMINAL, (ResourceCapability.TERMINAL_SESSION,), (ResourceCapability.TERMINAL_SESSION, ResourceCapability.TERMINAL_CANCEL, ResourceCapability.INSPECT), ResourceBudget(20, 100), timedelta(minutes=5), "lease"))
    adapter = ReferenceTerminalAdapter()
    return TerminalService(resource_manager=manager, adapter=adapter), manager, lease, context


class Sink:
    def __init__(self) -> None:
        self.chunks = []
        self.outcome = None

    def emit(self, chunk):
        self.chunks.append(chunk)
        return StreamDisposition.CONTINUE

    def close(self, outcome):
        self.outcome = outcome


def test_stream_is_bounded_and_input_retry_is_idempotent() -> None:
    service, _manager, lease, context = make()
    adapter = service.adapter
    adapter.register_result("interactive", stdout=b"abcdefghij", complete=False)
    limits = TerminalLimits(timedelta(minutes=5), timedelta(seconds=5), 1, 1024, timedelta(seconds=5), 32, 32, 32, "network:denied")
    created = service.create(CreateTerminalSession("create", context, lease.lease_id, WorkspacePath.root(), "shell:reference", (), limits, "create"))
    command = TerminalCommand("command", created.id, context, "interactive", WorkspacePath.root(), (), timedelta(seconds=5), 32, "command")
    service.execute(ExecuteTerminalCommand(command))
    input_request = WriteTerminalInput("input", context, lease.lease_id, created.id, command.command_id, b"x", False, 1, "input")
    first = service.write_input(input_request)
    second = service.write_input(input_request)
    assert isinstance(first, InputWriteResult)
    assert first == second
    sink = Sink()
    result = service.stream(StreamTerminalOutput("stream", context, lease.lease_id, created.id, command.command_id, 0, 2, 4, timedelta(seconds=1)), sink)
    assert result.bytes_emitted == 4
    assert result.truncated is True
    assert sink.chunks[0].sequence == 1


def test_completed_command_is_not_changed_by_late_cancel() -> None:
    service, _manager, lease, context = make()
    adapter = service.adapter
    adapter.register_result("done", stdout=b"ok", complete=True)
    limits = TerminalLimits(timedelta(minutes=5), timedelta(seconds=5), 1, 1024, timedelta(seconds=5), 32, 32, 32, "network:denied")
    created = service.create(CreateTerminalSession("create", context, lease.lease_id, WorkspacePath.root(), "shell:reference", (), limits, "create"))
    command = TerminalCommand("command", created.id, context, "done", WorkspacePath.root(), (), timedelta(seconds=5), 32, "command")
    service.execute(ExecuteTerminalCommand(command))
    cancel = __import__("agentos.terminal.models", fromlist=["CancelTerminalCommand"]).CancelTerminalCommand("cancel", context, lease.lease_id, created.id, command.command_id, "USER_REQUESTED", service.now() + timedelta(seconds=1), "cancel")
    result = service.request_cancel(cancel)
    assert result.stage.value == "ALREADY_EXITED"
