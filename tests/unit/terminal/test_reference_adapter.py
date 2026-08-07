from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentos.filesystem.models import WorkspacePath
from agentos.terminal.models import (
    AuthorizedTerminalQuery,
    CancelTerminalCommand,
    CancellationReason,
    CreateTerminalSession,
    ExecuteTerminalCommand,
    StreamDisposition,
    StreamTerminalOutput,
    TerminalCommand,
    TerminalLimits,
    TerminalOperationContext,
    TerminalSessionStatus,
    TerminationStage,
    WriteTerminalInput,
)
from agentos.terminal.reference import ReferenceTerminalAdapter


def context() -> TerminalOperationContext:
    return TerminalOperationContext("u", "ws", "a", "e", "c", "terminal.execute", "agent:a")


def limits() -> TerminalLimits:
    return TerminalLimits(timedelta(minutes=5), timedelta(seconds=30), 2, 1024, timedelta(seconds=30), 8, 8, 8, "network:denied")


def create_request() -> CreateTerminalSession:
    return CreateTerminalSession("create-1", context(), "lease-1", WorkspacePath.root(), "shell:reference", (), limits(), "create-key")


class Sink:
    def __init__(self) -> None:
        self.chunks = []
        self.closed = None

    def emit(self, chunk):
        self.chunks.append(chunk)
        return StreamDisposition.CONTINUE

    def close(self, outcome):
        self.closed = outcome


def command(session_id: str, text: str = "echo ok", command_id: str = "command-1") -> TerminalCommand:
    return TerminalCommand(command_id, session_id, context(), text, WorkspacePath.root(), (), timedelta(seconds=5), 8, "command-key")


def test_reference_adapter_is_deterministic_and_sequences_bounded_output() -> None:
    adapter = ReferenceTerminalAdapter()
    created = adapter.create_session(create_request())
    assert created.snapshot.status is TerminalSessionStatus.READY
    adapter.register_result("echo ok", stdout=b"123456789", exit_code=0)
    accepted = adapter.execute(ExecuteTerminalCommand(command(created.session_id, "echo ok")))
    assert accepted.command_id == "command-1"
    sink = Sink()
    stream = adapter.stream(StreamTerminalOutput("stream-1", context(), "lease-1", created.session_id, "command-1", 0, 10, 4, timedelta(seconds=1)), sink)
    assert stream.bytes_emitted == 4
    assert stream.truncated is True
    assert [chunk.sequence for chunk in sink.chunks] == [1]
    assert adapter.outcome("command-1").exit_code == 0


def test_reference_adapter_rejects_foreign_session_and_preserves_input_idempotency() -> None:
    adapter = ReferenceTerminalAdapter()
    created = adapter.create_session(create_request())
    accepted = adapter.write_input(WriteTerminalInput("input-1", context(), "lease-1", created.session_id, "missing", b"x", False, 1, "input-key"))
    assert accepted.code.value == "COMMAND_NOT_FOUND"
    request = WriteTerminalInput("input-1", context(), "lease-1", created.session_id, "command-1", b"x", False, 1, "input-key")
    adapter.register_running_command(command(created.session_id, "interactive", "command-1"))
    first = adapter.write_input(request)
    second = adapter.write_input(request)
    assert first == second
    assert first.accepted_bytes == 1


def test_reference_adapter_cancel_and_tree_cleanup_are_owned_and_idempotent() -> None:
    adapter = ReferenceTerminalAdapter()
    created = adapter.create_session(create_request())
    adapter.register_running_command(command(created.session_id, "interactive", "command-1"))
    cancelled = adapter.cancel(CancelTerminalCommand("cancel-1", context(), "lease-1", created.session_id, "command-1", CancellationReason.USER_REQUESTED, datetime.now(timezone.utc) + timedelta(seconds=1), "cancel-key"))
    assert cancelled.stage in (TerminationStage.COOPERATIVE, TerminationStage.ALREADY_EXITED)
    assert adapter.supervisor().reconcile(created.session_id, context()).ownership_confirmed is True
    closed = adapter.close(__import__("agentos.terminal.models", fromlist=["CloseTerminalSession"]).CloseTerminalSession("close-1", context(), "lease-1", created.session_id, TerminalSessionStatus.READY, "done", datetime.now(timezone.utc) + timedelta(seconds=1), "close-key"))
    assert closed.effect_state.value == "APPLIED"
    assert adapter.close(__import__("agentos.terminal.models", fromlist=["CloseTerminalSession"]).CloseTerminalSession("close-1", context(), "lease-1", created.session_id, TerminalSessionStatus.CLOSED, "done", datetime.now(timezone.utc) + timedelta(seconds=1), "close-key")) == closed
