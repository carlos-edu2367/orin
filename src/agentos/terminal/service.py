from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock

from agentos.events.models import DataClassification, EventEnvelope
from agentos.filesystem.models import WorkspacePath
from agentos.resources.models import (
    AuthorizeResourceOperation,
    ResourceCapability,
    ResourceError,
    ResourceLeaseState,
    ReleaseResourceLease,
)

from .models import (
    AuthorizedTerminalQuery,
    CancelTerminalCommand,
    CancelTerminalResult,
    CloseTerminalResult,
    CloseTerminalSession,
    CommandExited,
    CreateTerminalSession,
    CancellationReason,
    ExecuteTerminalCommand,
    InputWriteResult,
    StreamResult,
    StreamTerminalOutput,
    TerminalCommandAccepted,
    TerminalCommandOutcome,
    TerminalEffectState,
    TerminalError,
    TerminalErrorCode,
    TerminalOperationContext,
    TerminalSessionSnapshot,
    TerminalSessionStatus,
    WriteTerminalInput,
)
from .ports import TerminalAdapter, TerminalOutputSink, TerminalPort
from .reference import ReferenceSessionHandle, ReferenceTerminalAdapter


class InMemoryTerminalEventSink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    def append(self, event: EventEnvelope) -> None:
        self.events.append(event)


class TerminalService(TerminalPort):
    """Reference Terminal authority; lease and Workspace authorities stay external."""

    def __init__(self, *, resource_manager=None, workspace_manager=None, adapter: TerminalAdapter | None = None, event_sink=None, clock=None, persistence_journal=None, artifact_manager=None) -> None:
        self.resource_manager = resource_manager
        self.workspace_manager = workspace_manager
        self.adapter = adapter or ReferenceTerminalAdapter(clock=clock)
        self.event_sink = event_sink or InMemoryTerminalEventSink()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.persistence_journal = persistence_journal
        self.artifact_manager = artifact_manager
        self._lock = RLock()
        self._sessions: dict[str, TerminalSessionSnapshot] = {}
        self._session_contexts: dict[str, TerminalOperationContext] = {}
        self._session_leases: dict[str, str] = {}
        self._create_idempotency: dict[tuple[tuple[str, ...], str], tuple[tuple[object, ...], TerminalSessionSnapshot]] = {}
        self._operation_idempotency: dict[tuple[str, str, str], tuple[tuple[object, ...], object]] = {}
        self._outcomes: dict[str, TerminalCommandOutcome] = {}
        self._accepted_at: dict[str, datetime] = {}
        self._sequences: dict[str, int] = {}

    def now(self) -> datetime:
        return self._clock()

    @staticmethod
    def _error(code: TerminalErrorCode, *, effect_state=TerminalEffectState.NOT_APPLIED, reason_code="terminal operation rejected") -> TerminalError:
        return TerminalError(code, effect_state=effect_state, reason_code=reason_code)

    @staticmethod
    def _same_context(left: TerminalOperationContext, right: TerminalOperationContext) -> bool:
        return left.scope_key() == right.scope_key()

    def _event(self, event_type: str, context: TerminalOperationContext, session_id: str, *, command_id: str | None = None, status: str | None = None, reason_code: str | None = None, output_bytes: int | None = None) -> None:
        sequence = self._sequences.get(context.execution_id, 0) + 1
        self._sequences[context.execution_id] = sequence
        payload: dict[str, object] = {"session_id": session_id}
        if command_id is not None:
            payload["command_id"] = command_id
        if status is not None:
            payload["status"] = status
        if reason_code is not None:
            payload["reason_code"] = reason_code[:64]
        if output_bytes is not None:
            payload["output_bytes"] = output_bytes
        self.event_sink.append(EventEnvelope(event_id=f"terminal-event:{session_id}:{sequence}", event_type=event_type, event_version=1, occurred_at=self.now(), source="terminal", correlation_id=context.correlation_id, causation_id=session_id, sequence=sequence, user_id=context.user_id, workspace_id=context.workspace_id, agent_id=context.agent_id, execution_id=context.execution_id, classification=DataClassification.INTERNAL, payload=payload))

    def _resource_context(self, context: TerminalOperationContext):
        return context

    def _validate_workspace(self, context: TerminalOperationContext) -> TerminalError | None:
        if self.workspace_manager is None:
            return None
        try:
            from agentos.workspaces.models import InspectWorkspace, WorkspaceOperationContext, WorkspaceState
            workspace_context = WorkspaceOperationContext(context.user_id, context.workspace_id, context.agent_id, context.execution_id, context.correlation_id, "workspace.terminal", context.actor)
            snapshot = self.workspace_manager.inspect(InspectWorkspace(workspace_context))
        except (TypeError, ValueError):
            return self._error(TerminalErrorCode.CWD_REJECTED)
        if isinstance(snapshot, object) and hasattr(snapshot, "code"):
            return self._error(TerminalErrorCode.CWD_REJECTED)
        if getattr(snapshot, "state", None) is not WorkspaceState.ACTIVE or getattr(snapshot, "root_descriptor", None) is None:
            return self._error(TerminalErrorCode.CWD_REJECTED)
        return None

    def _authorize(self, context: TerminalOperationContext, lease_id: str, operation_id: str, capability: ResourceCapability) -> TerminalError | None:
        if self.resource_manager is None:
            return None
        lease = self.resource_manager.inspect(context=self._resource_context(context), lease_id=lease_id)
        if isinstance(lease, ResourceError):
            return self._resource_error(lease)
        if lease.state is not ResourceLeaseState.LEASED:
            return self._error(TerminalErrorCode.LEASE_INVALID)
        handle = self.resource_manager.authorize(AuthorizeResourceOperation(lease_id, operation_id, self._resource_context(context), capability, requested_usage_operations=1))
        if isinstance(handle, ResourceError):
            return self._resource_error(handle)
        return None

    @staticmethod
    def _resource_error(error: ResourceError) -> TerminalError:
        mapping = {
            "LEASE_EXPIRED": TerminalErrorCode.LEASE_INVALID,
            "LEASE_RELEASED": TerminalErrorCode.LEASE_INVALID,
            "LEASE_REVOKED": TerminalErrorCode.LEASE_INVALID,
            "FENCE_REJECTED": TerminalErrorCode.FENCE_REJECTED,
            "CAPABILITY_DENIED": TerminalErrorCode.POLICY_DENIED,
            "UNAUTHORIZED": TerminalErrorCode.UNAUTHORIZED,
            "NOT_FOUND": TerminalErrorCode.NOT_FOUND,
            "BUDGET_EXCEEDED": TerminalErrorCode.LIMIT_EXCEEDED,
        }
        return TerminalError(mapping.get(error.code.value, TerminalErrorCode.ADAPTER_FAILURE), effect_state=TerminalEffectState.UNKNOWN if getattr(error.effect_state, "value", "") == "UNKNOWN" else TerminalEffectState.NOT_APPLIED)

    @staticmethod
    def _fingerprint(*values: object) -> tuple[object, ...]:
        return tuple(values)

    def _session(self, session_id: str, context: TerminalOperationContext, lease_id: str) -> TerminalSessionSnapshot | TerminalError:
        snapshot = self._sessions.get(session_id)
        stored_context = self._session_contexts.get(session_id)
        if snapshot is None or stored_context is None:
            return self._error(TerminalErrorCode.NOT_FOUND)
        if not self._same_context(stored_context, context) or self._session_leases.get(session_id) != lease_id:
            return self._error(TerminalErrorCode.UNAUTHORIZED)
        authorization_error = self._authorize(context, lease_id, f"inspect:{session_id}", ResourceCapability.INSPECT)
        if authorization_error is not None:
            return authorization_error
        return snapshot

    def _record_snapshot(self, snapshot: TerminalSessionSnapshot, context: TerminalOperationContext, operation_id: str, event_type: str | None = None) -> None:
        self._sessions[str(snapshot.id)] = snapshot
        if self.persistence_journal is not None:
            self.persistence_journal.record(snapshot, context=context, operation_id=operation_id, event_type=event_type)

    def create(self, request: CreateTerminalSession) -> TerminalSessionSnapshot | TerminalError:
        with self._lock:
            key = (request.context.scope_key(), request.idempotency_key)
            fingerprint = self._fingerprint(request.lease_id, request.initial_cwd, request.shell_profile, request.environment_refs, request.limits)
            previous = self._create_idempotency.get(key)
            if previous is not None:
                return previous[1] if previous[0] == fingerprint else self._error(TerminalErrorCode.IDEMPOTENCY_CONFLICT)
            if not isinstance(request.initial_cwd, WorkspacePath):
                return self._error(TerminalErrorCode.CWD_REJECTED)
            workspace_error = self._validate_workspace(request.context)
            if workspace_error is not None:
                return workspace_error
            authorization_error = self._authorize(request.context, str(request.lease_id), str(request.request_id), ResourceCapability.TERMINAL_SESSION)
            if authorization_error is not None:
                return authorization_error
            handle = self.adapter.create_session(request)
            if isinstance(handle, TerminalError):
                return handle
            if not isinstance(handle, ReferenceSessionHandle):
                snapshot = getattr(handle, "snapshot", None)
                if snapshot is None:
                    return self._error(TerminalErrorCode.ADAPTER_FAILURE)
            else:
                snapshot = handle.snapshot
            self._session_contexts[str(snapshot.id)] = request.context
            self._session_leases[str(snapshot.id)] = str(request.lease_id)
            self._create_idempotency[key] = (fingerprint, snapshot)
            self._record_snapshot(snapshot, request.context, str(request.request_id), "TerminalSessionCreated")
            self._event("TerminalSessionCreated", request.context, str(snapshot.id), status=snapshot.status.value)
            return snapshot

    def execute(self, request: ExecuteTerminalCommand) -> TerminalCommandAccepted | TerminalError:
        with self._lock:
            command = request.command
            session = self._session(str(command.session_id), command.context, self._session_leases.get(str(command.session_id), ""))
            if isinstance(session, TerminalError):
                return session
            workspace_error = self._validate_workspace(command.context)
            if workspace_error is not None:
                return workspace_error
            lease_id = self._session_leases[str(command.session_id)]
            if request.expected_session_status is not session.status:
                return self._error(TerminalErrorCode.SESSION_STATE_REJECTED)
            if command.context.scope_key() != self._session_contexts[str(command.session_id)].scope_key():
                return self._error(TerminalErrorCode.UNAUTHORIZED)
            if session.status is not TerminalSessionStatus.READY:
                return self._error(TerminalErrorCode.COMMAND_ACTIVE if session.status is TerminalSessionStatus.RUNNING else TerminalErrorCode.SESSION_STATE_REJECTED)
            idem = (str(command.session_id), "execute", command.idempotency_key or str(command.command_id))
            fingerprint = self._fingerprint(command.command_id, command.command, command.requested_cwd, command.environment_refs, command.timeout, command.maximum_output_bytes)
            previous = self._operation_idempotency.get(idem)
            if previous is not None:
                return previous[1] if previous[0] == fingerprint else self._error(TerminalErrorCode.IDEMPOTENCY_CONFLICT)
            authorization_error = self._authorize(command.context, lease_id, str(command.command_id), ResourceCapability.TERMINAL_SESSION)
            if authorization_error is not None:
                return authorization_error
            accepted = self.adapter.execute(request)
            if isinstance(accepted, TerminalError):
                return accepted
            self._operation_idempotency[idem] = (fingerprint, accepted)
            self._accepted_at[str(command.command_id)] = accepted.accepted_at
            self._event("TerminalCommandStarted", command.context, str(command.session_id), command_id=str(command.command_id), status="RUNNING")
            adapter_snapshot = getattr(self.adapter, "snapshot", lambda _sid: None)(str(command.session_id))
            if adapter_snapshot is not None:
                self._record_snapshot(adapter_snapshot, command.context, str(command.command_id), None)
            outcome = getattr(self.adapter, "outcome", lambda _cid: None)(str(command.command_id))
            if outcome is not None:
                self._outcomes[str(command.command_id)] = outcome
                self._event("TerminalCommandFinished", command.context, str(command.session_id), command_id=str(command.command_id), status=type(outcome).__name__)
            return accepted

    def write_input(self, request) -> InputWriteResult | TerminalError:
        with self._lock:
            session = self._session(str(request.session_id), request.context, str(request.lease_id))
            if isinstance(session, TerminalError):
                return session
            if session.status is not TerminalSessionStatus.RUNNING:
                return self._error(TerminalErrorCode.SESSION_STATE_REJECTED)
            idem = (str(request.session_id), "input", request.idempotency_key)
            fingerprint = self._fingerprint(request.command_id, request.input_sequence, request.input, request.end_of_input)
            previous = self._operation_idempotency.get(idem)
            if previous is not None:
                return previous[1] if previous[0] == fingerprint else self._error(TerminalErrorCode.IDEMPOTENCY_CONFLICT)
            result = self.adapter.write_input(request)
            if isinstance(result, TerminalError):
                return result
            self._operation_idempotency[idem] = (fingerprint, result)
            return result

    def stream(self, request: StreamTerminalOutput, sink: TerminalOutputSink) -> StreamResult | TerminalError:
        with self._lock:
            session = self._session(str(request.session_id), request.context, str(request.lease_id))
            if isinstance(session, TerminalError):
                return session
            authorization_error = self._authorize(request.context, str(request.lease_id), str(request.request_id), ResourceCapability.TERMINAL_SESSION)
            if authorization_error is not None:
                return authorization_error
            result = self.adapter.stream(request, sink)
            if isinstance(result, TerminalError):
                return result
            snapshot = getattr(self.adapter, "snapshot", lambda _sid: None)(str(request.session_id))
            if snapshot is not None:
                prior_snapshot = self._sessions.get(str(request.session_id))
                if prior_snapshot is not None and prior_snapshot.output_ref is not None:
                    snapshot = replace(snapshot, output_ref=prior_snapshot.output_ref)
                self._record_snapshot(snapshot, request.context, str(request.request_id), None)
            overflow = bool(result.truncated or (snapshot is not None and snapshot.buffer.truncation.value != "NONE"))
            if overflow and request.command_id is not None and self.artifact_manager is not None and snapshot is not None and snapshot.output_ref is None and hasattr(self.adapter, "chunks_for"):
                reference = self.artifact_manager.publish_output(
                    context=request.context,
                    session_id=str(request.session_id),
                    command_id=str(request.command_id),
                    chunks=self.adapter.chunks_for(str(request.session_id), str(request.command_id)),
                    idempotency_key=f"terminal-output:{request.session_id}:{request.command_id}",
                    maximum_bytes=snapshot.buffer.maximum_bytes,
                )
                if isinstance(reference, str) and reference:
                    snapshot = replace(snapshot, output_ref=reference)
                    self._record_snapshot(snapshot, request.context, str(request.request_id), None)
                    outcome = self._outcomes.get(str(request.command_id))
                    if isinstance(outcome, CommandExited):
                        self._outcomes[str(request.command_id)] = replace(outcome, output_ref=reference)
            if overflow:
                result = replace(result, truncated=True)
            self._event("TerminalOutputProgressed", request.context, str(request.session_id), command_id=str(request.command_id) if request.command_id is not None else None, output_bytes=result.bytes_emitted)
            return result

    def inspect(self, query: AuthorizedTerminalQuery) -> TerminalSessionSnapshot | TerminalError:
        with self._lock:
            session = self._session(str(query.session_id), query.context, str(query.lease_id))
            if isinstance(session, TerminalError):
                return session
            return session

    def request_cancel(self, request: CancelTerminalCommand) -> CancelTerminalResult | TerminalError:
        with self._lock:
            session = self._session(str(request.session_id), request.context, str(request.lease_id))
            if isinstance(session, TerminalError):
                return session
            idem = (str(request.session_id), "cancel", request.idempotency_key)
            fingerprint = self._fingerprint(request.command_id, request.reason, request.cancellation_deadline)
            previous = self._operation_idempotency.get(idem)
            if previous is not None:
                return previous[1] if previous[0] == fingerprint else self._error(TerminalErrorCode.IDEMPOTENCY_CONFLICT)
            authorization_error = self._authorize(request.context, str(request.lease_id), str(request.request_id), ResourceCapability.TERMINAL_CANCEL)
            if authorization_error is not None:
                return authorization_error
            result = self.adapter.cancel(request)
            if isinstance(result, TerminalError):
                return result
            self._operation_idempotency[idem] = (fingerprint, result)
            adapter_snapshot = getattr(self.adapter, "snapshot", lambda _sid: None)(str(request.session_id))
            if adapter_snapshot is not None:
                self._record_snapshot(adapter_snapshot, request.context, str(request.request_id), None)
            self._event("TerminalCommandFinished", request.context, str(request.session_id), command_id=str(request.command_id), status="CANCELLED")
            return result

    def close(self, request: CloseTerminalSession) -> CloseTerminalResult | TerminalError:
        with self._lock:
            session = self._session(str(request.session_id), request.context, str(request.lease_id))
            if isinstance(session, TerminalError):
                existing = next((result for (sid, kind, key), (_fingerprint, result) in self._operation_idempotency.items() if sid == str(request.session_id) and kind == "close" and key == request.idempotency_key), None)
                return existing if existing is not None else session
            idem = (str(request.session_id), "close", request.idempotency_key)
            fingerprint = self._fingerprint(request.expected_status, request.reason, request.cleanup_deadline)
            previous = self._operation_idempotency.get(idem)
            if previous is not None:
                return previous[1] if previous[0] == fingerprint else self._error(TerminalErrorCode.IDEMPOTENCY_CONFLICT)
            result = self.adapter.close(request)
            if isinstance(result, TerminalError):
                return result
            if self.resource_manager is not None:
                lease = self.resource_manager.inspect(context=self._resource_context(request.context), lease_id=str(request.lease_id))
                if isinstance(lease, ResourceError):
                    recovery = replace(result, status=TerminalSessionStatus.RECOVERY_REQUIRED, effect_state=TerminalEffectState.UNKNOWN, lease_released=False)
                    self._operation_idempotency[idem] = (fingerprint, recovery)
                    return recovery
                released = self.resource_manager.release(ReleaseResourceLease(str(request.request_id), str(request.lease_id), self._resource_context(request.context), lease.fencing_token, request.reason, request.idempotency_key))
                if isinstance(released, ResourceError) or getattr(released.effect_state, "value", "") != "APPLIED":
                    recovery = replace(result, status=TerminalSessionStatus.RECOVERY_REQUIRED, effect_state=TerminalEffectState.UNKNOWN, lease_released=False)
                    self._operation_idempotency[idem] = (fingerprint, recovery)
                    self._record_snapshot(replace(session, status=TerminalSessionStatus.RECOVERY_REQUIRED), request.context, str(request.request_id), None)
                    return recovery
            result = replace(result, lease_released=True)
            self._operation_idempotency[idem] = (fingerprint, result)
            snapshot = getattr(self.adapter, "snapshot", lambda _sid: None)(str(request.session_id))
            if snapshot is not None:
                self._record_snapshot(snapshot, request.context, str(request.request_id), "TerminalSessionClosed")
            self._event("TerminalSessionClosed", request.context, str(request.session_id), status=TerminalSessionStatus.CLOSED.value)
            return result

    def reconcile(self, session_id: str, context: TerminalOperationContext):
        with self._lock:
            lease_id = self._session_leases.get(session_id)
            if lease_id is None:
                return self._error(TerminalErrorCode.NOT_FOUND)
            snapshot = self._session(session_id, context, lease_id)
            if isinstance(snapshot, TerminalError):
                return snapshot
            if snapshot.current_command_id is not None:
                accepted_at = self._accepted_at.get(str(snapshot.current_command_id))
                if accepted_at is not None:
                    command_state = getattr(self.adapter, "_command", lambda _cid: None)(str(snapshot.current_command_id))
                    timeout = getattr(getattr(command_state, "request", None), "command", None)
                    if timeout is not None and self.now() >= accepted_at + timeout.timeout:
                        timeout_request = CancelTerminalCommand(
                            f"timeout:{snapshot.current_command_id}",
                            context,
                            lease_id,
                            session_id,
                            str(snapshot.current_command_id),
                            CancellationReason.TIMEOUT,
                            self.now(),
                            f"timeout:{snapshot.current_command_id}",
                        )
                        timeout_result = self.adapter.cancel(timeout_request)
                        if not isinstance(timeout_result, TerminalError):
                            updated_snapshot = getattr(self.adapter, "snapshot", lambda _sid: snapshot)(session_id)
                            self._record_snapshot(updated_snapshot, context, timeout_request.request_id, None)
                            self._event("TerminalCommandFinished", context, session_id, command_id=str(snapshot.current_command_id), status="TIMEOUT")
                            return updated_snapshot
            observed = self.adapter.supervisor().reconcile(session_id, context)
            if not observed.ownership_confirmed:
                updated = replace(snapshot, status=TerminalSessionStatus.RECOVERY_REQUIRED)
                self._record_snapshot(updated, context, "reconcile:" + session_id, "TerminalSessionLost")
                self._event("TerminalSessionLost", context, session_id, status=updated.status.value, reason_code="OWNERSHIP_UNCONFIRMED")
                return updated
            return observed

    def restore(self, context: TerminalOperationContext, session_id: str) -> TerminalSessionSnapshot | TerminalError:
        with self._lock:
            if self.persistence_journal is None:
                return self._error(TerminalErrorCode.RECOVERY_REQUIRED)
            snapshot = self.persistence_journal.load(context, session_id)
            if snapshot is None:
                return self._error(TerminalErrorCode.NOT_FOUND)
            self._sessions[session_id] = snapshot
            self._session_contexts[session_id] = context
            self._session_leases[session_id] = str(snapshot.lease_id)
            return snapshot


__all__ = ["InMemoryTerminalEventSink", "TerminalService"]
