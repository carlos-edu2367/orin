from __future__ import annotations

from datetime import timedelta
import sys
import time

from agentos.filesystem.models import WorkspacePath
from agentos.terminal.models import CreateTerminalSession, ExecuteTerminalCommand, TerminalCommand, TerminalError, TerminalLimits, TerminalOperationContext
from agentos.terminal.local import LocalTerminalAdapter


def context() -> TerminalOperationContext:
    return TerminalOperationContext("u", "ws", "a", "e", "c", "terminal.execute", "agent:a")


def limits() -> TerminalLimits:
    return TerminalLimits(timedelta(minutes=5), timedelta(seconds=5), 2, 32 * 1024 * 1024, timedelta(seconds=5), 1024, 1024, 1024, "network:denied")


class Resolver:
    def __init__(self, physical: str) -> None:
        self.physical = physical

    def resolve(self, _context, _path):
        return self.physical

    def revalidate(self, _context, _path):
        return True


def test_local_adapter_executes_without_shell_and_returns_output() -> None:
    adapter = LocalTerminalAdapter(Resolver("."))
    request = CreateTerminalSession("create", context(), "lease", WorkspacePath.root(), "shell:local", (), limits(), "create")
    created = adapter.create_session(request)
    assert not isinstance(created, TerminalError)
    command_text = f'"{sys.executable}" -c "print(\'ok\')"'
    command = TerminalCommand("command", created.session_id, context(), command_text, WorkspacePath.root(), (), timedelta(seconds=5), 1024, "command")
    accepted = adapter.execute(ExecuteTerminalCommand(command))
    assert accepted.command_id == "command"
    deadline = time.monotonic() + 5
    outcome = None
    while time.monotonic() < deadline and outcome is None:
        outcome = adapter.outcome("command")
        time.sleep(0.01)
    assert outcome is not None
    assert outcome.exit_code == 0
    assert b"ok" in b"".join(chunk.bytes for chunk in adapter.chunks_for(created.session_id, "command"))


def test_local_adapter_requires_private_cwd_resolver() -> None:
    adapter = LocalTerminalAdapter(None)
    result = adapter.create_session(CreateTerminalSession("create", context(), "lease", WorkspacePath.root(), "shell:local", (), limits(), "create"))
    assert isinstance(result, TerminalError)
    assert result.code.value == "CWD_REJECTED"
