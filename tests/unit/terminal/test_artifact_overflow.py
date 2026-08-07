from __future__ import annotations

from datetime import timedelta

from agentos.filesystem.models import WorkspacePath
from agentos.resources.models import ResourceBudget, ResourceCapability, ResourceLeaseRequest, ResourceOperationContext, ResourceType
from agentos.resources.service import ResourceManagerService
from agentos.terminal.models import CreateTerminalSession, ExecuteTerminalCommand, StreamDisposition, StreamTerminalOutput, TerminalCommand, TerminalLimits, TerminalOperationContext
from agentos.terminal.reference import ReferenceTerminalAdapter
from agentos.terminal.service import TerminalService


def context() -> TerminalOperationContext:
    return TerminalOperationContext("u", "ws", "a", "e", "c", "terminal.execute", "agent:a")


class ArtifactWriter:
    def __init__(self, result="artifact-ref:1") -> None:
        self.calls = []
        self.result = result

    def publish_output(self, *, context, session_id, command_id, chunks, idempotency_key, maximum_bytes):
        self.calls.append((context, session_id, command_id, tuple(chunks), idempotency_key, maximum_bytes))
        return self.result


class Sink:
    def emit(self, chunk):
        return StreamDisposition.CONTINUE

    def close(self, outcome):
        pass


def make_service(writer=None):
    ctx = context()
    manager = ResourceManagerService()
    resource_context = ResourceOperationContext(*ctx.scope_key())
    lease = manager.acquire(ResourceLeaseRequest("request", resource_context, ResourceType.TERMINAL, (ResourceCapability.TERMINAL_SESSION,), (ResourceCapability.TERMINAL_SESSION, ResourceCapability.INSPECT), ResourceBudget(20, 100), timedelta(minutes=5), "lease"))
    adapter = ReferenceTerminalAdapter()
    service = TerminalService(resource_manager=manager, adapter=adapter, artifact_manager=writer)
    limits = TerminalLimits(timedelta(minutes=5), timedelta(seconds=5), 1, 1024, timedelta(seconds=5), 32, 32, 4, "network:denied")
    created = service.create(CreateTerminalSession("create", ctx, lease.lease_id, WorkspacePath.root(), "shell:reference", (), limits, "create"))
    adapter.register_result("large", stdout=b"abcdefgh", complete=True)
    command = TerminalCommand("command", created.id, ctx, "large", WorkspacePath.root(), (), timedelta(seconds=5), 32, "command")
    service.execute(ExecuteTerminalCommand(command))
    return service, lease, ctx, created, command


def test_overflow_can_publish_one_authorized_artifact_reference() -> None:
    writer = ArtifactWriter()
    service, lease, ctx, created, command = make_service(writer)
    result = service.stream(StreamTerminalOutput("stream", ctx, lease.lease_id, created.id, command.command_id, 0, 10, 32, timedelta(seconds=1)), Sink())
    assert result.truncated is True
    assert writer.calls and writer.calls[0][1:3] == (created.id, command.command_id)
    assert service.inspect(__import__("agentos.terminal.models", fromlist=["AuthorizedTerminalQuery"]).AuthorizedTerminalQuery(ctx, lease.lease_id, created.id)).output_ref == "artifact-ref:1"
    service.stream(StreamTerminalOutput("stream-2", ctx, lease.lease_id, created.id, command.command_id, 0, 10, 32, timedelta(seconds=1)), Sink())
    assert len(writer.calls) == 1


def test_overflow_without_writer_remains_explicitly_truncated() -> None:
    service, lease, ctx, created, command = make_service(None)
    result = service.stream(StreamTerminalOutput("stream", ctx, lease.lease_id, created.id, command.command_id, 0, 10, 32, timedelta(seconds=1)), Sink())
    assert result.truncated is True
    assert service.inspect(__import__("agentos.terminal.models", fromlist=["AuthorizedTerminalQuery"]).AuthorizedTerminalQuery(ctx, lease.lease_id, created.id)).buffer.truncation.value == "HEAD_DROPPED"
