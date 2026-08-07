from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from agentos.filesystem.models import WorkspacePath

from .models import (
    AuthorizedTerminalQuery,
    BufferTruncation,
    CancelTerminalCommand,
    CancelTerminalResult,
    CancellationReason,
    CloseTerminalResult,
    CloseTerminalSession,
    CommandCancelled,
    CommandExited,
    CreateTerminalSession,
    ExecuteTerminalCommand,
    InputWriteResult,
    OutputChannel,
    ProcessExitState,
    ProcessTreeSnapshot,
    ResourceUsage,
    SignalReceipt,
    StreamDisposition,
    StreamResult,
    StreamTerminalOutput,
    TerminalBuffer,
    TerminalCommandAccepted,
    TerminalCommandOutcome,
    TerminalEffectState,
    TerminalError,
    TerminalErrorCode,
    TerminalOperationContext,
    TerminalSessionId,
    TerminalSessionSnapshot,
    TerminalSessionStatus,
    TerminationResult,
    TerminationStage,
    WriteTerminalInput,
)


@dataclass(frozen=True, slots=True)
class ReferenceSessionHandle:
    session_id: str
    snapshot: TerminalSessionSnapshot

    def __repr__(self) -> str:
        return "ReferenceSessionHandle(<ephemeral>)"


@dataclass(frozen=True, slots=True)
class _RegisteredResult:
    stdout: bytes
    stderr: bytes
    exit_code: int
    final_cwd: WorkspacePath
    complete: bool


@dataclass
class _CommandState:
    request: ExecuteTerminalCommand
    chunks: list[object]
    outcome: TerminalCommandOutcome | None
    input_sequences: dict[str, InputWriteResult]
    cancelled: bool = False


@dataclass
class _SessionState:
    snapshot: TerminalSessionSnapshot
    commands: dict[str, _CommandState]
    current_command_id: str | None = None
    closed: bool = False
    close_receipts: dict[str, CloseTerminalResult] | None = None


class _ReferenceSupervisor:
    def __init__(self, adapter: "ReferenceTerminalAdapter") -> None:
        self._adapter = adapter

    def signal(self, command_id: str, signal: str) -> SignalReceipt:
        with self._adapter._lock:
            state = self._adapter._command(command_id)
            if state is None:
                return SignalReceipt(command_id, TerminationStage.UNKNOWN, TerminalEffectState.NOT_APPLIED)
            if state.outcome is not None:
                return SignalReceipt(command_id, TerminationStage.ALREADY_EXITED)
            state.cancelled = True
            state.outcome = CommandCancelled(command_id, TerminationStage(signal), effect_state=TerminalEffectState.APPLIED)
            return SignalReceipt(command_id, TerminationStage(signal))

    def await_exit(self, command_id: str, deadline: datetime) -> ProcessExitState:
        with self._adapter._lock:
            state = self._adapter._command(command_id)
            if state is None or state.outcome is None:
                return ProcessExitState(command_id, False, None, TerminalEffectState.NOT_APPLIED)
            exit_code = state.outcome.exit_code if isinstance(state.outcome, CommandExited) else None
            return ProcessExitState(command_id, True, exit_code)

    def terminate_tree(self, session_id: str, deadline: datetime) -> TerminationResult:
        with self._adapter._lock:
            session = self._adapter._sessions.get(session_id)
            if session is None:
                return TerminationResult(session_id, TerminationStage.UNKNOWN, False, TerminalEffectState.NOT_APPLIED)
            if session.current_command_id:
                state = session.commands.get(session.current_command_id)
                if state and state.outcome is None:
                    state.cancelled = True
                    state.outcome = CommandCancelled(session.current_command_id, TerminationStage.TERMINATE)
            return TerminationResult(session_id, TerminationStage.ALREADY_EXITED, True)

    def reconcile(self, session_id: str, context: TerminalOperationContext) -> ProcessTreeSnapshot:
        with self._adapter._lock:
            session = self._adapter._sessions.get(session_id)
            owned = session is not None and session.snapshot.execution_id == context.execution_id and session.snapshot.workspace == context.workspace_id and session.snapshot.owner == context.user_id
            live = 0
            if session and session.current_command_id:
                current = session.commands.get(session.current_command_id)
                live = int(current is not None and current.outcome is None)
            return ProcessTreeSnapshot(session_id, 1 if owned else 0, live, owned)


class ReferenceTerminalAdapter:
    """Deterministic terminal adapter; it never invokes a host process."""

    def __init__(self, *, clock=None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._counter = 0
        self._sessions: dict[str, _SessionState] = {}
        self._registered: dict[str, _RegisteredResult] = {}
        self._idempotency: dict[tuple[str, str], object] = {}
        self._supervisor = _ReferenceSupervisor(self)

    def now(self) -> datetime:
        return self._clock()

    def register_result(self, command: str, *, stdout: bytes = b"", stderr: bytes = b"", exit_code: int = 0, final_cwd: WorkspacePath | None = None, complete: bool = True) -> None:
        self._registered[command] = _RegisteredResult(stdout, stderr, exit_code, final_cwd or WorkspacePath.root(), complete)

    def register_running_command(self, command) -> None:
        with self._lock:
            session = self._sessions.get(str(command.session_id))
            if session is None:
                raise ValueError("session does not exist")
            request = ExecuteTerminalCommand(command)
            session.commands[str(command.command_id)] = _CommandState(request, [], None, {})
            session.current_command_id = str(command.command_id)
            session.snapshot = replace(session.snapshot, status=TerminalSessionStatus.RUNNING, current_command_id=str(command.command_id), last_activity_at=self.now())

    def create_session(self, request: CreateTerminalSession) -> ReferenceSessionHandle | TerminalError:
        with self._lock:
            self._counter += 1
            session_id = f"terminal-session:{self._counter}"
            now = self.now()
            snapshot = TerminalSessionSnapshot(session_id, request.initial_cwd, None, TerminalSessionStatus.READY, request.context.user_id, request.context.workspace_id, request.context.agent_id, request.context.execution_id, request.context.correlation_id, request.context.purpose, TerminalBuffer(None, None, 0, 0, request.limits.maximum_buffer_bytes), request.lease_id, None, 1, now, now, now + request.limits.session_ttl)
            self._sessions[session_id] = _SessionState(snapshot, {}, close_receipts={})
            return ReferenceSessionHandle(session_id, snapshot)

    def _command(self, command_id: str) -> _CommandState | None:
        for session in self._sessions.values():
            if command_id in session.commands:
                return session.commands[command_id]
        return None

    def _session_for_command(self, command_id: str) -> _SessionState | None:
        for session in self._sessions.values():
            if command_id in session.commands:
                return session
        return None

    def execute(self, request: ExecuteTerminalCommand) -> TerminalCommandAccepted | TerminalError:
        with self._lock:
            command = request.command
            session = self._sessions.get(str(command.session_id))
            if session is None:
                return TerminalError(TerminalErrorCode.NOT_FOUND)
            if session.current_command_id is not None:
                active = session.commands[session.current_command_id]
                if active.outcome is None:
                    return TerminalError(TerminalErrorCode.COMMAND_ACTIVE)
            key = (str(command.session_id), command.idempotency_key or str(command.command_id))
            if key in self._idempotency:
                return self._idempotency[key]
            registered = self._registered.get(command.command, _RegisteredResult(b"", b"", 0, command.requested_cwd or session.snapshot.cwd, True))
            chunks = []
            sequence = 1
            if registered.stdout:
                chunks.append(self._chunk(str(command.session_id), str(command.command_id), sequence, OutputChannel.STDOUT, registered.stdout))
                sequence += 1
            if registered.stderr:
                chunks.append(self._chunk(str(command.session_id), str(command.command_id), sequence, OutputChannel.STDERR, registered.stderr))
            outcome = None
            if registered.complete:
                outcome = CommandExited(str(command.command_id), registered.exit_code, registered.final_cwd, None, ResourceUsage(output_bytes=len(registered.stdout) + len(registered.stderr)))
            state = _CommandState(request, chunks, outcome, {})
            session.commands[str(command.command_id)] = state
            session.current_command_id = str(command.command_id)
            accepted = TerminalCommandAccepted(str(command.command_id), str(command.session_id), self.now())
            self._idempotency[key] = accepted
            if outcome is not None:
                session.current_command_id = None
                session.snapshot = replace(session.snapshot, status=TerminalSessionStatus.READY, cwd=registered.final_cwd, last_activity_at=self.now())
            else:
                session.snapshot = replace(session.snapshot, status=TerminalSessionStatus.RUNNING, current_command_id=str(command.command_id), last_activity_at=self.now())
            return accepted

    @staticmethod
    def _chunk(session_id: str, command_id: str, sequence: int, channel: OutputChannel, payload: bytes):
        from .models import TerminalOutputChunk
        return TerminalOutputChunk(session_id, command_id, sequence, channel, payload, datetime.now(timezone.utc))

    def outcome(self, command_id: str) -> TerminalCommandOutcome | None:
        with self._lock:
            state = self._command(command_id)
            return None if state is None else state.outcome

    def chunks_for(self, session_id: str, command_id: str) -> tuple[object, ...]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or command_id not in session.commands:
                return ()
            return tuple(session.commands[command_id].chunks)

    def snapshot(self, session_id: str) -> TerminalSessionSnapshot | None:
        with self._lock:
            state = self._sessions.get(session_id)
            return None if state is None else state.snapshot

    def write_input(self, request: WriteTerminalInput) -> InputWriteResult | TerminalError:
        with self._lock:
            session = self._sessions.get(str(request.session_id))
            state = self._command(request.command_id)
            if session is None or state is None or str(request.command_id) not in session.commands:
                return TerminalError(TerminalErrorCode.COMMAND_NOT_FOUND)
            previous = state.input_sequences.get(request.idempotency_key)
            if previous is not None:
                return previous
            result = InputWriteResult(request.session_id, request.command_id, request.input_sequence, len(request.input))
            state.input_sequences[request.idempotency_key] = result
            return result

    def stream(self, request: StreamTerminalOutput, sink) -> StreamResult | TerminalError:
        with self._lock:
            session = self._sessions.get(str(request.session_id))
            if session is None:
                return TerminalError(TerminalErrorCode.NOT_FOUND)
            chunks = []
            for command_id, state in session.commands.items():
                if request.command_id is not None and command_id != str(request.command_id):
                    continue
                chunks.extend(chunk for chunk in state.chunks if chunk.sequence > request.after_sequence)
            chunks.sort(key=lambda chunk: chunk.sequence)
            emitted = 0
            emitted_bytes = 0
            truncated = False
            next_sequence = None
            for chunk in chunks:
                if emitted >= request.maximum_chunks or emitted_bytes >= request.maximum_bytes:
                    truncated = True
                    break
                remaining = request.maximum_bytes - emitted_bytes
                payload = chunk.bytes[:remaining]
                if not payload:
                    truncated = True
                    break
                if len(payload) != len(chunk.bytes):
                    truncated = True
                    from .models import TerminalOutputChunk
                    chunk = TerminalOutputChunk(chunk.session_id, chunk.command_id, chunk.sequence, chunk.channel, payload, chunk.occurred_at)
                disposition = sink.emit(chunk)
                emitted += 1
                emitted_bytes += len(payload)
                next_sequence = chunk.sequence + 1
                if disposition in (StreamDisposition.PAUSE, StreamDisposition.STOP):
                    break
            outcome = self.outcome(str(request.command_id)) if request.command_id is not None else None
            sink.close(outcome)
            total_bytes = sum(len(item.bytes) for state in session.commands.values() for item in state.chunks)
            maximum = session.snapshot.buffer.maximum_bytes
            retained = min(total_bytes, maximum)
            dropped = max(0, total_bytes - maximum)
            sequences = [item.sequence for state in session.commands.values() for item in state.chunks]
            session.snapshot = replace(
                session.snapshot,
                buffer=TerminalBuffer(
                    min(sequences) if sequences else None,
                    max(sequences) if sequences else None,
                    retained,
                    dropped,
                    maximum,
                    BufferTruncation.HEAD_DROPPED if dropped else BufferTruncation.NONE,
                ),
                last_activity_at=self.now(),
            )
            return StreamResult(request.session_id, request.command_id, emitted, emitted_bytes, next_sequence, truncated)

    def cancel(self, request: CancelTerminalCommand) -> CancelTerminalResult | TerminalError:
        with self._lock:
            session = self._sessions.get(str(request.session_id))
            state = self._command(request.command_id)
            if session is None or state is None or str(request.command_id) not in session.commands:
                return TerminalError(TerminalErrorCode.COMMAND_NOT_FOUND)
            if state.outcome is not None:
                return CancelTerminalResult(request.session_id, request.command_id, TerminationStage.ALREADY_EXITED, TerminalEffectState.APPLIED)
            state.cancelled = True
            state.outcome = CommandCancelled(request.command_id, TerminationStage.COOPERATIVE)
            session.current_command_id = None
            session.snapshot = replace(session.snapshot, status=TerminalSessionStatus.READY, current_command_id=None, last_activity_at=self.now())
            return CancelTerminalResult(request.session_id, request.command_id, TerminationStage.COOPERATIVE, TerminalEffectState.APPLIED)

    def close(self, request: CloseTerminalSession) -> CloseTerminalResult | TerminalError:
        with self._lock:
            session = self._sessions.get(str(request.session_id))
            if session is None:
                return TerminalError(TerminalErrorCode.NOT_FOUND)
            previous = (session.close_receipts or {}).get(request.idempotency_key)
            if previous is not None:
                return previous
            session.current_command_id = None
            session.closed = True
            session.snapshot = replace(session.snapshot, status=TerminalSessionStatus.CLOSED, current_command_id=None, last_activity_at=self.now())
            result = CloseTerminalResult(request.session_id, TerminalSessionStatus.CLOSED, TerminalEffectState.APPLIED, False)
            (session.close_receipts or {})[request.idempotency_key] = result
            return result

    def inspect(self, query: AuthorizedTerminalQuery) -> TerminalSessionSnapshot | TerminalError:
        snapshot = self.snapshot(str(query.session_id))
        return snapshot if snapshot is not None else TerminalError(TerminalErrorCode.NOT_FOUND)

    def supervisor(self) -> _ReferenceSupervisor:
        return self._supervisor


__all__ = ["ReferenceSessionHandle", "ReferenceTerminalAdapter"]
