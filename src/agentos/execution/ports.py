from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from .events import AuditRecord, DataClassification, OutboxEntry
from .models import (
    CancellationReason,
    CommandId,
    CorrelationId,
    Execution,
    ExecutionFailure,
    ExecutionId,
    ExecutionState,
    IdempotencyKey,
    ResultReference,
    TransactionId,
    UserId,
    Version,
    WorkspaceId,
)


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")


@dataclass(frozen=True, slots=True)
class ExecutionCommandContext:
    user_id: UserId
    workspace_id: WorkspaceId | None
    agent_id: str
    execution_id: ExecutionId
    correlation_id: CorrelationId
    purpose: str

    def __post_init__(self) -> None:
        _required(self.user_id, "user_id")
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        _required(self.agent_id, "agent_id")
        _required(self.execution_id, "execution_id")
        _required(self.correlation_id, "correlation_id")
        _required(self.purpose, "purpose")


@dataclass(frozen=True, slots=True)
class ExecutionCommand:
    context: ExecutionCommandContext
    command_id: CommandId
    idempotency_key: IdempotencyKey
    expected_version: Version | None
    requested_at: datetime

    def __post_init__(self) -> None:
        _required(self.command_id, "command_id")
        _required(self.idempotency_key, "idempotency_key")
        if self.requested_at.tzinfo is None or self.requested_at.utcoffset() is None:
            raise ValueError("requested_at must be timezone-aware")
        if self.expected_version is not None and self.expected_version < 1:
            raise ValueError("expected_version must be positive when supplied")


@dataclass(frozen=True, slots=True)
class CreateExecution(ExecutionCommand):
    execution: Execution


@dataclass(frozen=True, slots=True)
class AcquireExecution(ExecutionCommand):
    worker_ref: str

    def __post_init__(self) -> None:
        ExecutionCommand.__post_init__(self)
        _required(self.worker_ref, "worker_ref")


@dataclass(frozen=True, slots=True)
class TransitionExecution(ExecutionCommand):
    target_state: ExecutionState
    reason_code: str
    result_ref: ResultReference | None = None
    failure: ExecutionFailure | None = None
    cancellation_reason: CancellationReason | None = None

    def __post_init__(self) -> None:
        ExecutionCommand.__post_init__(self)
        _required(self.reason_code, "reason_code")


@dataclass(frozen=True, slots=True)
class CancelExecution(ExecutionCommand):
    reason: CancellationReason


@dataclass(frozen=True, slots=True)
class PauseExecution(ExecutionCommand):
    pass


@dataclass(frozen=True, slots=True)
class ResumeExecution(ExecutionCommand):
    pass


@dataclass(frozen=True, slots=True)
class ProvideExecutionInput(ExecutionCommand):
    input_ref: str

    def __post_init__(self) -> None:
        ExecutionCommand.__post_init__(self)
        _required(self.input_ref, "input_ref")


@dataclass(frozen=True, slots=True)
class ExecutionRelatedChange:
    kind: str
    reference: str | None = None
    duration_seconds: int = 0
    iterations: int = 0
    provider_tokens: int = 0
    cost: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        _required(self.kind, "change kind")
        if self.reference is not None:
            _required(self.reference, "change reference")
        if self.duration_seconds < 0 or self.iterations < 0 or self.provider_tokens < 0:
            raise ValueError("related change deltas cannot be negative")
        if self.cost < 0:
            raise ValueError("related change cost cannot be negative")


@dataclass(frozen=True, slots=True)
class CommitExecutionChanges(ExecutionCommand):
    expected_state: ExecutionState
    target_state: ExecutionState
    reason_code: str
    result_ref: ResultReference | None = None
    failure: ExecutionFailure | None = None
    cancellation_reason: CancellationReason | None = None
    changes: tuple[ExecutionRelatedChange, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        ExecutionCommand.__post_init__(self)
        _required(self.reason_code, "reason_code")


@dataclass(frozen=True, slots=True)
class ExecutionControlQuery:
    context: ExecutionCommandContext


class ControlSignal(StrEnum):
    CONTINUE = "CONTINUE"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"


class RejectionReason(StrEnum):
    INVALID_COMMAND = "INVALID_COMMAND"
    UNAUTHORIZED = "UNAUTHORIZED"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    TERMINAL_IMMUTABLE = "TERMINAL_IMMUTABLE"
    VERSION_REQUIRED = "VERSION_REQUIRED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    PERSISTENCE_REJECTED = "PERSISTENCE_REJECTED"


@dataclass(frozen=True, slots=True)
class Accepted:
    resulting_version: Version
    transaction_id: TransactionId | None = None


@dataclass(frozen=True, slots=True)
class AlreadyApplied:
    resulting_version: Version
    transaction_id: TransactionId | None = None


@dataclass(frozen=True, slots=True)
class Rejected:
    reason: RejectionReason
    current_state: ExecutionState | None


@dataclass(frozen=True, slots=True)
class Conflict:
    current_version: Version


@dataclass(frozen=True, slots=True)
class Indeterminate:
    transaction_id: TransactionId


CommandResult = Accepted | AlreadyApplied | Rejected | Conflict | Indeterminate


class TransactionCommitState(StrEnum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class TransactionReceipt:
    transaction_id: TransactionId
    commit_state: TransactionCommitState
    affected_execution_id: ExecutionId
    resulting_version: Version
    outbox_event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecutionDomainChange:
    execution_id: ExecutionId
    expected_version: Version | None
    new_execution: Execution


@dataclass(frozen=True, slots=True)
class TransactionRequest:
    transaction_id: TransactionId
    context: ExecutionCommandContext
    idempotency_key: IdempotencyKey
    operation_fingerprint: str
    change: ExecutionDomainChange
    audit: AuditRecord
    outbox: OutboxEntry
    classification: DataClassification = DataClassification.INTERNAL


@dataclass(frozen=True, slots=True)
class TransactionCommitted:
    receipt: TransactionReceipt
    execution: Execution
    already_applied: bool = False


@dataclass(frozen=True, slots=True)
class TransactionRejected:
    reason: RejectionReason


@dataclass(frozen=True, slots=True)
class TransactionConflicted:
    current_version: Version


@dataclass(frozen=True, slots=True)
class TransactionIndeterminate:
    transaction_id: TransactionId


TransactionResult = TransactionCommitted | TransactionRejected | TransactionConflicted | TransactionIndeterminate


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    operation_fingerprint: str
    receipt: TransactionReceipt
    execution: Execution


class ExecutionNotFoundError(LookupError):
    pass


class UnauthorizedExecutionError(PermissionError):
    pass


class ExecutionControl(Protocol):
    def load(self, context: ExecutionCommandContext) -> Execution: ...

    def create(self, command: CreateExecution) -> CommandResult: ...

    def acquire(self, command: AcquireExecution) -> CommandResult: ...

    def current_signal(self, query: ExecutionControlQuery) -> ControlSignal: ...

    def request_cancel(self, command: CancelExecution) -> CommandResult: ...

    def request_pause(self, command: PauseExecution) -> CommandResult: ...

    def request_resume(self, command: ResumeExecution) -> CommandResult: ...

    def provide_input(self, command: ProvideExecutionInput) -> CommandResult: ...

    def transition(self, command: TransitionExecution) -> CommandResult: ...

    def commit(self, command: CommitExecutionChanges) -> CommandResult: ...


class TransactionalPersistence(Protocol):
    def get(self, execution_id: ExecutionId, context: ExecutionCommandContext | None = None) -> Execution: ...

    def lookup_idempotency(
        self, context: ExecutionCommandContext, idempotency_key: IdempotencyKey
    ) -> IdempotencyRecord | None: ...

    def transact(self, request: TransactionRequest) -> TransactionResult: ...

    def inspect_commit(
        self,
        *,
        context: ExecutionCommandContext,
        transaction_id: TransactionId,
        idempotency_key: IdempotencyKey,
    ) -> TransactionReceipt: ...
