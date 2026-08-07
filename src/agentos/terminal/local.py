from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import os
import shlex
import signal
import subprocess
from threading import Thread
from typing import Mapping, Protocol

from agentos.filesystem.models import WorkspacePath

from .models import (
    CancelTerminalCommand,
    CommandExited,
    CreateTerminalSession,
    ExecuteTerminalCommand,
    InputWriteResult,
    ResourceUsage,
    SignalReceipt,
    TerminalError,
    TerminalErrorCode,
    TerminalOutputChunk,
    TerminalSessionStatus,
    TerminationResult,
    TerminationStage,
)
from .reference import ReferenceTerminalAdapter


class LocalWorkspaceCwdResolver(Protocol):
    def resolve(self, context, path: WorkspacePath) -> str | None: ...
    def revalidate(self, context, path: WorkspacePath) -> bool: ...


class _LocalSupervisor:
    def __init__(self, adapter: "LocalTerminalAdapter") -> None:
        self.adapter = adapter
        self.delegate = adapter._delegate_supervisor

    def signal(self, command_id: str, signal_name: str) -> SignalReceipt:
        return self.delegate.signal(command_id, signal_name)

    def await_exit(self, command_id: str, deadline):
        self.adapter._collect_command(command_id)
        return self.delegate.await_exit(command_id, deadline)

    def terminate_tree(self, session_id: str, deadline) -> TerminationResult:
        self.adapter._terminate_session_processes(session_id)
        return self.delegate.terminate_tree(session_id, deadline)

    def reconcile(self, session_id: str, context):
        self.adapter._collect_session(session_id)
        return self.delegate.reconcile(session_id, context)


class LocalTerminalAdapter(ReferenceTerminalAdapter):
    """Operational local adapter; all host process details stay inside this module."""

    def __init__(self, cwd_resolver: LocalWorkspaceCwdResolver | None, *, secret_resolver=None, environment_allowlist: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.cwd_resolver = cwd_resolver
        self.secret_resolver = secret_resolver or (lambda _reference: {})
        self.environment_allowlist = frozenset(environment_allowlist)
        self._processes: dict[str, subprocess.Popen] = {}
        self._delegate_supervisor = super().supervisor()
        self._local_supervisor = _LocalSupervisor(self)

    def create_session(self, request: CreateTerminalSession):
        if self.cwd_resolver is None or self.cwd_resolver.resolve(request.context, request.initial_cwd) is None:
            return TerminalError(TerminalErrorCode.CWD_REJECTED)
        return super().create_session(request)

    def _environment(self, refs: tuple[str, ...]) -> dict[str, str]:
        result: dict[str, str] = {}
        for ref in refs:
            values = self.secret_resolver(ref)
            for key, value in values.items():
                if key in self.environment_allowlist and isinstance(value, str):
                    result[key] = value
        return result

    def execute(self, request: ExecuteTerminalCommand):
        if self.cwd_resolver is None:
            return TerminalError(TerminalErrorCode.CWD_REJECTED)
        logical_cwd = request.command.requested_cwd or WorkspacePath.root()
        if not self.cwd_resolver.revalidate(request.command.context, logical_cwd):
            return TerminalError(TerminalErrorCode.CWD_REJECTED)
        physical_cwd = self.cwd_resolver.resolve(request.command.context, logical_cwd)
        if physical_cwd is None:
            return TerminalError(TerminalErrorCode.CWD_REJECTED)
        try:
            argv = shlex.split(request.command.command, posix=True)
        except ValueError:
            return TerminalError(TerminalErrorCode.INVALID_REQUEST)
        if not argv:
            return TerminalError(TerminalErrorCode.INVALID_REQUEST)
        if str(request.command.session_id) not in self._sessions:
            return TerminalError(TerminalErrorCode.NOT_FOUND)
        self.register_result(request.command.command, final_cwd=logical_cwd, complete=False)
        accepted = super().execute(request)
        if isinstance(accepted, TerminalError):
            return accepted
        try:
            process = subprocess.Popen(
                argv,
                cwd=physical_cwd,
                env=self._environment(tuple(str(ref) for ref in request.command.environment_refs)),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
        except (OSError, ValueError):
            session = self._sessions[str(request.command.session_id)]
            state = session.commands[str(request.command.command_id)]
            state.outcome = TerminalError(TerminalErrorCode.ADAPTER_FAILURE)  # type: ignore[assignment]
            session.current_command_id = None
            session.snapshot = replace(session.snapshot, status=TerminalSessionStatus.FAILED, current_command_id=None)
            return TerminalError(TerminalErrorCode.ADAPTER_FAILURE)
        self._processes[str(request.command.command_id)] = process
        for channel, stream in (("STDOUT", process.stdout), ("STDERR", process.stderr)):
            Thread(target=self._read_stream, args=(str(request.command.session_id), str(request.command.command_id), channel, stream), daemon=True).start()
        return accepted

    def _read_stream(self, session_id: str, command_id: str, channel: str, stream) -> None:
        while True:
            payload = stream.readline()
            if not payload:
                return
            with self._lock:
                session = self._sessions.get(session_id)
                if session is None or command_id not in session.commands:
                    return
                state = session.commands[command_id]
                sequence = len(state.chunks) + 1
                state.chunks.append(TerminalOutputChunk(session_id, command_id, sequence, channel, payload, datetime.now(timezone.utc)))
                maximum = session.snapshot.buffer.maximum_bytes
                retained = 0
                while state.chunks and (retained + len(state.chunks[-1].bytes)) > maximum:
                    retained = sum(len(item.bytes) for item in state.chunks)
                    if retained <= maximum:
                        break
                    state.chunks.pop(0)

    def _collect_command(self, command_id: str) -> None:
        with self._lock:
            process = self._processes.get(command_id)
            state = self._command(command_id)
            session = self._session_for_command(command_id)
            if process is None or state is None or session is None or state.outcome is not None:
                return
            exit_code = process.poll()
            if exit_code is None:
                return
            output_bytes = sum(len(item.bytes) for item in state.chunks)
            state.outcome = CommandExited(command_id, exit_code, state.request.command.requested_cwd or session.snapshot.cwd, None, ResourceUsage(output_bytes=output_bytes))
            session.current_command_id = None
            session.snapshot = replace(session.snapshot, status=TerminalSessionStatus.READY, current_command_id=None, cwd=state.request.command.requested_cwd or session.snapshot.cwd, last_activity_at=datetime.now(timezone.utc))

    def _collect_session(self, session_id: str) -> None:
        for command_id in tuple(self._processes):
            if self._session_for_command(command_id) and self._session_for_command(command_id).snapshot.id == session_id:
                self._collect_command(command_id)

    def outcome(self, command_id: str):
        self._collect_command(command_id)
        return super().outcome(command_id)

    def _terminate_session_processes(self, session_id: str) -> None:
        for command_id, process in tuple(self._processes.items()):
            session = self._session_for_command(command_id)
            if session is None or str(session.snapshot.id) != session_id or process.poll() is not None:
                continue
            try:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                continue

    def cancel(self, request: CancelTerminalCommand):
        process = self._processes.get(str(request.command_id))
        if process is not None and process.poll() is None:
            try:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGINT)
            except (OSError, ProcessLookupError):
                process = None
        return super().cancel(request)

    def close(self, request):
        self._terminate_session_processes(str(request.session_id))
        return super().close(request)

    def supervisor(self):
        return self._local_supervisor


__all__ = ["LocalTerminalAdapter", "LocalWorkspaceCwdResolver"]
