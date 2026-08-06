from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Mapping
from uuid import uuid4

from .events import AuditRecord, DataClassification, EventEnvelope, ExecutionEventType, OutboxEntry
from .models import (
    CancellationReason,
    Execution,
    ExecutionFailure,
    ExecutionResult,
    ExecutionState,
    Version,
)
from .ports import (
    Accepted,
    AcquireExecution,
    AlreadyApplied,
    CancelExecution,
    CommandResult,
    CommitExecutionChanges,
    Conflict,
    ControlSignal,
    CreateExecution,
    ExecutionCommand,
    ExecutionCommandContext,
    ExecutionControlQuery,
    ExecutionDomainChange,
    ExecutionRelatedChange,
    ExecutionNotFoundError,
    Indeterminate,
    PauseExecution,
    ProvideExecutionInput,
    Rejected,
    RejectionReason,
    ResumeExecution,
    TransactionConflicted,
    TransactionRequest,
    TransactionIndeterminate,
    TransactionRejected,
    TransactionalPersistence,
    TransactionCommitted,
    TransitionExecution,
    UnauthorizedExecutionError,
)


_ALLOWED_TRANSITIONS: Mapping[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.QUEUED: frozenset({ExecutionState.STARTING, ExecutionState.CANCELLED}),
    ExecutionState.STARTING: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.QUEUED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
        }
    ),
    ExecutionState.RUNNING: frozenset(
        {
            ExecutionState.WAITING_TOOL,
            ExecutionState.WAITING_USER,
            ExecutionState.PAUSED,
            ExecutionState.QUEUED,
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
        }
    ),
    ExecutionState.WAITING_TOOL: frozenset(
        {
            ExecutionState.RUNNING,
            ExecutionState.PAUSED,
            ExecutionState.QUEUED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
        }
    ),
    ExecutionState.WAITING_USER: frozenset(
        {
            ExecutionState.QUEUED,
            ExecutionState.PAUSED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
        }
    ),
    ExecutionState.PAUSED: frozenset(
        {ExecutionState.QUEUED, ExecutionState.FAILED, ExecutionState.CANCELLED}
    ),
    ExecutionState.COMPLETED: frozenset(),
    ExecutionState.FAILED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
}

_EVENT_BY_TARGET: Mapping[ExecutionState, ExecutionEventType] = {
    ExecutionState.QUEUED: ExecutionEventType.EXECUTION_QUEUED,
    ExecutionState.STARTING: ExecutionEventType.EXECUTION_QUEUED,
    ExecutionState.RUNNING: ExecutionEventType.EXECUTION_STARTED,
    ExecutionState.WAITING_TOOL: ExecutionEventType.EXECUTION_WAITING_FOR_TOOL,
    ExecutionState.WAITING_USER: ExecutionEventType.EXECUTION_WAITING_FOR_USER,
    ExecutionState.PAUSED: ExecutionEventType.EXECUTION_PAUSED,
    ExecutionState.COMPLETED: ExecutionEventType.EXECUTION_FINISHED,
    ExecutionState.FAILED: ExecutionEventType.EXECUTION_FAILED,
    ExecutionState.CANCELLED: ExecutionEventType.EXECUTION_CANCELLED,
}


class ExecutionControlService:
    """RFC 102 state machine backed only by the TransactionalPersistence port."""

    def __init__(self, persistence: TransactionalPersistence) -> None:
        self._persistence = persistence

    def load(self, context: ExecutionCommandContext) -> Execution:
        return self._persistence.get(context.execution_id, context)

    def create(self, command: CreateExecution) -> CommandResult:
        execution = command.execution
        if command.expected_version is not None:
            return Rejected(RejectionReason.VERSION_REQUIRED, None)
        if not self._context_matches_execution(command.context, execution):
            return Rejected(RejectionReason.UNAUTHORIZED, None)
        if execution.state is not ExecutionState.QUEUED or execution.state_version != 1:
            return Rejected(RejectionReason.INVALID_COMMAND, execution.state)

        fingerprint = self._fingerprint(command, target=execution.state, reason_code="create")
        prior = self._prior_result(command, fingerprint, None)
        if prior is not None:
            return prior
        return self._submit(
            command=command,
            current=None,
            new_execution=execution,
            reason_code="create",
            event_type=ExecutionEventType.EXECUTION_QUEUED,
            fingerprint=fingerprint,
            from_state=None,
        )

    def acquire(self, command: AcquireExecution) -> CommandResult:
        return self._perform(
            command,
            target_state=ExecutionState.STARTING,
            reason_code="dispatch_acquired",
            precondition=ExecutionState.QUEUED,
        )

    def current_signal(self, query: ExecutionControlQuery) -> ControlSignal:
        self._persistence.get(query.context.execution_id, query.context)
        return ControlSignal.CONTINUE

    def request_cancel(self, command: CancelExecution) -> CommandResult:
        return self._perform(
            command,
            target_state=ExecutionState.CANCELLED,
            reason_code=command.reason.code,
            cancellation_reason=command.reason,
        )

    def request_pause(self, command: PauseExecution) -> CommandResult:
        return self._perform(
            command,
            target_state=ExecutionState.PAUSED,
            reason_code="pause_requested",
        )

    def request_resume(self, command: ResumeExecution) -> CommandResult:
        return self._perform(
            command,
            target_state=ExecutionState.QUEUED,
            reason_code="resume_requested",
            precondition=ExecutionState.PAUSED,
        )

    def provide_input(self, command: ProvideExecutionInput) -> CommandResult:
        return self._perform(
            command,
            target_state=ExecutionState.QUEUED,
            reason_code="input_attached",
            precondition=ExecutionState.WAITING_USER,
            payload_extra={"input_ref": command.input_ref},
        )

    def transition(self, command: TransitionExecution) -> CommandResult:
        return self._perform(
            command,
            target_state=command.target_state,
            reason_code=command.reason_code,
            result_ref=command.result_ref,
            failure=command.failure,
            cancellation_reason=command.cancellation_reason,
        )

    def commit(self, command: CommitExecutionChanges) -> CommandResult:
        return self._perform(
            command,
            target_state=command.target_state,
            reason_code=command.reason_code,
            result_ref=command.result_ref,
            failure=command.failure,
            cancellation_reason=command.cancellation_reason,
            precondition=command.expected_state,
            changes=command.changes,
            payload_extra={
                "change_count": len(command.changes),
                "change_fingerprint": sha256(repr(command.changes).encode("utf-8")).hexdigest(),
            },
        )

    def _perform(
        self,
        command: ExecutionCommand,
        *,
        target_state: ExecutionState,
        reason_code: str,
        result_ref: str | None = None,
        failure: ExecutionFailure | None = None,
        cancellation_reason: CancellationReason | None = None,
        precondition: ExecutionState | None = None,
        changes: tuple[ExecutionRelatedChange, ...] = (),
        payload_extra: Mapping[str, object] | None = None,
    ) -> CommandResult:
        try:
            current = self._persistence.get(command.context.execution_id, command.context)
        except (ExecutionNotFoundError, UnauthorizedExecutionError):
            return Rejected(RejectionReason.UNAUTHORIZED, None)

        fingerprint = self._fingerprint(
            command,
            target=target_state,
            reason_code=reason_code,
            result_ref=result_ref,
            failure=failure,
            cancellation_reason=cancellation_reason,
            payload_extra=payload_extra,
        )
        prior = self._prior_result(command, fingerprint, current)
        if prior is not None:
            return prior
        if command.expected_version is None:
            return Rejected(RejectionReason.VERSION_REQUIRED, current.state)
        if current.state_version != command.expected_version:
            return Conflict(current.state_version)
        if precondition is not None and current.state is not precondition:
            return Rejected(RejectionReason.INVALID_TRANSITION, current.state)
        if current.state in {
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
        }:
            return Rejected(RejectionReason.TERMINAL_IMMUTABLE, current.state)
        if target_state not in _ALLOWED_TRANSITIONS[current.state]:
            return Rejected(RejectionReason.INVALID_TRANSITION, current.state)
        if target_state is ExecutionState.COMPLETED and result_ref is None:
            return Rejected(RejectionReason.INVALID_COMMAND, current.state)
        if target_state is ExecutionState.FAILED and failure is None:
            return Rejected(RejectionReason.INVALID_COMMAND, current.state)
        if target_state is ExecutionState.CANCELLED and cancellation_reason is None:
            return Rejected(RejectionReason.INVALID_COMMAND, current.state)

        resulting_version = Version(current.state_version + 1)
        new_usage = _apply_usage(current.usage, changes)
        context_manifest_ref = _reference_for(changes, "context-manifest-recorded")
        checkpoint_ref = _reference_for(changes, "checkpoint-recorded")
        new_execution = replace(
            current,
            state=target_state,
            state_version=resulting_version,
            causation_id=command.command_id,
            result=ExecutionResult(result_ref) if target_state is ExecutionState.COMPLETED else None,
            failure=failure if target_state is ExecutionState.FAILED else None,
            cancellation_reason=(
                cancellation_reason if target_state is ExecutionState.CANCELLED else None
            ),
            usage=new_usage,
            iteration_count=current.iteration_count + sum(change.iterations for change in changes),
            context_manifest_ref=context_manifest_ref or current.context_manifest_ref,
            checkpoint_ref=checkpoint_ref or current.checkpoint_ref,
            started_at=(
                command.requested_at
                if target_state is ExecutionState.RUNNING and current.started_at is None
                else current.started_at
            ),
            updated_at=command.requested_at,
            finished_at=(
                command.requested_at
                if target_state
                in {ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.CANCELLED}
                else None
            ),
        )
        return self._submit(
            command=command,
            current=current,
            new_execution=new_execution,
            reason_code=reason_code,
            event_type=_event_type(current.state, target_state),
            fingerprint=fingerprint,
            from_state=current.state,
            payload_extra=payload_extra,
        )

    def _submit(
        self,
        *,
        command: ExecutionCommand,
        current: Execution | None,
        new_execution: Execution,
        reason_code: str,
        event_type: ExecutionEventType,
        fingerprint: str,
        from_state: ExecutionState | None,
        payload_extra: Mapping[str, object] | None = None,
    ) -> CommandResult:
        transaction_id = str(uuid4())
        event = EventEnvelope(
            event_id=str(uuid4()),
            event_type=event_type,
            event_version=1,
            occurred_at=command.requested_at,
            source="execution-control",
            correlation_id=command.context.correlation_id,
            causation_id=command.command_id,
            sequence=new_execution.state_version,
            ownership=new_execution.ownership,
            execution_id=new_execution.execution_id,
            classification=DataClassification.INTERNAL,
            payload={
                "from_state": from_state.value if from_state is not None else None,
                "to_state": new_execution.state.value,
                "state_version": new_execution.state_version,
                "reason_code": reason_code,
                **(payload_extra or {}),
            },
        )
        audit = AuditRecord(
            audit_id=str(uuid4()),
            user_id=command.context.user_id,
            workspace_id=command.context.workspace_id,
            agent_id=command.context.agent_id,
            execution_id=command.context.execution_id,
            correlation_id=command.context.correlation_id,
            purpose=command.context.purpose,
            command_id=command.command_id,
            decision="APPLIED",
            from_state=from_state.value if from_state is not None else "NONE",
            to_state=new_execution.state.value,
            resulting_version=new_execution.state_version,
        )
        request = TransactionRequest(
            transaction_id=transaction_id,
            context=command.context,
            idempotency_key=command.idempotency_key,
            operation_fingerprint=fingerprint,
            change=ExecutionDomainChange(
                execution_id=new_execution.execution_id,
                expected_version=current.state_version if current is not None else None,
                new_execution=new_execution,
            ),
            audit=audit,
            outbox=OutboxEntry(
                event=event,
                source_execution_id=new_execution.execution_id,
                expected_source_version=new_execution.state_version,
            ),
        )
        result = self._persistence.transact(request)
        if isinstance(result, TransactionCommitted):
            if result.already_applied:
                return AlreadyApplied(result.receipt.resulting_version, result.receipt.transaction_id)
            return Accepted(result.receipt.resulting_version, result.receipt.transaction_id)
        if isinstance(result, TransactionConflicted):
            return Conflict(result.current_version)
        if isinstance(result, TransactionIndeterminate):
            return Indeterminate(result.transaction_id)
        if isinstance(result, TransactionRejected):
            return Rejected(result.reason, current.state if current is not None else None)
        raise AssertionError("unknown transaction result")

    def _prior_result(
        self,
        command: ExecutionCommand,
        fingerprint: str,
        current: Execution | None,
    ) -> CommandResult | None:
        record = self._persistence.lookup_idempotency(command.context, command.idempotency_key)
        if record is None:
            return None
        if record.operation_fingerprint != fingerprint:
            return Rejected(RejectionReason.IDEMPOTENCY_CONFLICT, current.state if current else None)
        return AlreadyApplied(record.receipt.resulting_version, record.receipt.transaction_id)

    @staticmethod
    def _context_matches_execution(context: ExecutionCommandContext, execution: Execution) -> bool:
        return (
            context.execution_id == execution.execution_id
            and context.user_id == execution.ownership.user_id
            and context.workspace_id == execution.ownership.workspace_id
            and context.agent_id == execution.agent_id
            and context.correlation_id == execution.correlation_id
        )

    @staticmethod
    def _fingerprint(command: ExecutionCommand, **values: object) -> str:
        parts = [type(command).__name__, str(command.expected_version)]
        for key in sorted(values):
            value = values[key]
            if isinstance(value, (ExecutionFailure, CancellationReason)):
                value = repr(value)
            parts.append(f"{key}={value!r}")
        return sha256("|".join(parts).encode("utf-8")).hexdigest()


def _event_type(source: ExecutionState, target: ExecutionState) -> ExecutionEventType:
    if target is ExecutionState.QUEUED and source is ExecutionState.PAUSED:
        return ExecutionEventType.EXECUTION_RESUMED
    return _EVENT_BY_TARGET[target]


def _apply_usage(current, changes):
    return replace(
        current,
        duration_seconds=current.duration_seconds
        + sum(change.duration_seconds for change in changes),
        iterations=current.iterations + sum(change.iterations for change in changes),
        provider_tokens=current.provider_tokens
        + sum(change.provider_tokens for change in changes),
        cost=current.cost + sum((change.cost for change in changes), start=current.cost * 0),
    )


def _reference_for(changes, kind):
    for change in changes:
        if change.kind == kind and change.reference is not None:
            return change.reference
    return None
