from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentos.filesystem.models import WorkspacePath
from agentos.terminal.models import (
    BufferTruncation,
    TerminalBuffer,
    TerminalCommand,
    TerminalCommandId,
    TerminalLimits,
    TerminalOperationContext,
    TerminalOutputChunk,
    TerminalSessionStatus,
)


def context(**changes: object) -> TerminalOperationContext:
    values = {
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "agent_id": "agent-1",
        "execution_id": "execution-1",
        "correlation_id": "correlation-1",
        "purpose": "terminal.execute",
        "actor": "agent:agent-1",
    }
    values.update(changes)
    return TerminalOperationContext(**values)


def test_terminal_context_is_complete_and_sensitive_repr_is_redacted() -> None:
    operation_context = context()
    assert operation_context.scope_key()[:4] == ("user-1", "workspace-1", "agent-1", "execution-1")
    assert "terminal.execute" not in repr(operation_context)
    with pytest.raises(ValueError):
        context(actor="")
    with pytest.raises(ValueError):
        context(workspace_id="../other")


def test_terminal_models_are_immutable_and_cwd_is_logical() -> None:
    command = TerminalCommand(
        command_id=TerminalCommandId("command-1"),
        session_id="session-1",
        context=context(),
        command="echo secret",
        requested_cwd=WorkspacePath.from_string("src"),
        environment_refs=("secret-ref-1",),
        timeout=timedelta(seconds=5),
        maximum_output_bytes=128,
        idempotency_key="command-key",
    )
    assert command.requested_cwd == WorkspacePath.from_string("src")
    assert "echo secret" not in repr(command)
    with pytest.raises(Exception):
        command.command = "changed"  # type: ignore[misc]


def test_buffer_and_chunk_metadata_are_bounded_and_sequenced() -> None:
    now = datetime.now(timezone.utc)
    buffer = TerminalBuffer(
        first_sequence=1,
        last_sequence=2,
        retained_bytes=4,
        dropped_bytes=8,
        maximum_bytes=4,
        truncation=BufferTruncation.HEAD_DROPPED,
    )
    chunk = TerminalOutputChunk("session-1", "command-1", 1, "STDOUT", b"ok", now)
    assert buffer.truncation is BufferTruncation.HEAD_DROPPED
    assert chunk.bytes == b"ok"
    assert "ok" not in repr(chunk)
    with pytest.raises(ValueError):
        TerminalOutputChunk("session-1", "command-1", 0, "STDOUT", b"x", now)


def test_limits_and_states_have_explicit_bounds() -> None:
    limits = TerminalLimits(
        session_ttl=timedelta(minutes=5),
        command_timeout=timedelta(seconds=30),
        maximum_processes=4,
        maximum_memory_bytes=1024,
        maximum_cpu_time=timedelta(seconds=30),
        maximum_output_bytes=256,
        maximum_input_bytes=128,
        maximum_buffer_bytes=128,
        network_policy_ref="network:denied",
    )
    assert limits.maximum_output_bytes == 256
    assert {status.value for status in TerminalSessionStatus} >= {
        "CREATING", "READY", "RUNNING", "EXITED", "FAILED", "CANCELLED", "CLOSED"
    }
    with pytest.raises(ValueError):
        TerminalLimits(
            session_ttl=timedelta(hours=2),
            command_timeout=timedelta(seconds=1),
            maximum_processes=1,
            maximum_memory_bytes=1,
            maximum_cpu_time=timedelta(seconds=1),
            maximum_output_bytes=1,
            maximum_input_bytes=1,
            maximum_buffer_bytes=1,
            network_policy_ref="network:denied",
        )
