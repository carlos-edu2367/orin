from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from decimal import Decimal
from typing import NewType

ExecutionId = NewType("ExecutionId", str)
UserId = NewType("UserId", str)
WorkspaceId = NewType("WorkspaceId", str)
AgentId = NewType("AgentId", str)
CorrelationId = NewType("CorrelationId", str)
CommandId = NewType("CommandId", str)
IdempotencyKey = NewType("IdempotencyKey", str)
TransactionId = NewType("TransactionId", str)
EventId = NewType("EventId", str)
Version = NewType("Version", int)
ResultReference = NewType("ResultReference", str)
ContextManifestReference = NewType("ContextManifestReference", str)
CheckpointReference = NewType("CheckpointReference", str)
TaskReference = NewType("TaskReference", str)
FailureReference = NewType("FailureReference", str)


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class ExecutionState(StrEnum):
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    WAITING_TOOL = "WAITING_TOOL"
    WAITING_USER = "WAITING_USER"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FailureReason(StrEnum):
    INVALID_INITIALIZATION = "INVALID_INITIALIZATION"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    TIMEOUT = "TIMEOUT"
    CHECKPOINT_INVALID = "CHECKPOINT_INVALID"
    INPUT_INVALID = "INPUT_INVALID"
    UNRECONCILABLE = "UNRECONCILABLE"


class CancellationReasonCode(StrEnum):
    USER_REQUESTED = "USER_REQUESTED"
    POLICY_REQUESTED = "POLICY_REQUESTED"
    TIMEOUT = "TIMEOUT"
    RECOVERY_ABORTED = "RECOVERY_ABORTED"


@dataclass(frozen=True, slots=True)
class Ownership:
    user_id: UserId
    workspace_id: WorkspaceId | None

    def __post_init__(self) -> None:
        _require_text(self.user_id, "user_id")
        if self.workspace_id is not None:
            _require_text(self.workspace_id, "workspace_id")


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_ref: TaskReference
    revision: int

    def __post_init__(self) -> None:
        _require_text(self.task_ref, "task_ref")
        if self.revision < 1:
            raise ValueError("task revision must be positive")


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    max_duration_seconds: int | None
    max_iterations: int | None
    max_cost: Decimal | None = None
    max_provider_tokens: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("max_duration_seconds", self.max_duration_seconds),
            ("max_iterations", self.max_iterations),
            ("max_provider_tokens", self.max_provider_tokens),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.max_cost is not None and self.max_cost < 0:
            raise ValueError("max_cost cannot be negative")


@dataclass(frozen=True, slots=True)
class ExecutionUsage:
    duration_seconds: int = 0
    iterations: int = 0
    provider_tokens: int = 0
    cost: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.duration_seconds < 0 or self.iterations < 0 or self.provider_tokens < 0:
            raise ValueError("execution usage cannot be negative")
        if self.cost < 0:
            raise ValueError("execution usage cost cannot be negative")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    result_ref: ResultReference

    def __post_init__(self) -> None:
        _require_text(self.result_ref, "result_ref")


@dataclass(frozen=True, slots=True)
class ExecutionFailure:
    code: FailureReason
    detail_ref: FailureReference | None = None

    def __post_init__(self) -> None:
        if self.detail_ref is not None:
            _require_text(self.detail_ref, "failure detail_ref")


@dataclass(frozen=True, slots=True)
class CancellationReason:
    code: CancellationReasonCode
    detail_ref: FailureReference | None = None

    def __post_init__(self) -> None:
        if self.detail_ref is not None:
            _require_text(self.detail_ref, "cancellation detail_ref")


@dataclass(frozen=True, slots=True)
class Execution:
    execution_id: ExecutionId
    ownership: Ownership
    agent_id: AgentId
    task: TaskSnapshot
    state: ExecutionState
    state_version: Version
    correlation_id: CorrelationId
    causation_id: EventId | CommandId | None
    parent_execution_id: ExecutionId | None
    context_manifest_ref: ContextManifestReference | None
    result: ExecutionResult | None
    failure: ExecutionFailure | None
    cancellation_reason: CancellationReason | None
    limits: ExecutionLimits
    usage: ExecutionUsage
    iteration_count: int
    created_at: datetime
    queued_at: datetime
    started_at: datetime | None
    updated_at: datetime
    finished_at: datetime | None
    checkpoint_ref: CheckpointReference | None = None
    agent_config_version: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.execution_id, "execution_id")
        _require_text(self.agent_id, "agent_id")
        _require_text(self.correlation_id, "correlation_id")
        if self.state_version < 1:
            raise ValueError("state_version must be positive")
        if self.iteration_count < 0:
            raise ValueError("iteration_count cannot be negative")
        if self.agent_config_version is not None and self.agent_config_version < 1:
            raise ValueError("agent_config_version must be positive when supplied")
        for name, value in (
            ("created_at", self.created_at),
            ("queued_at", self.queued_at),
            ("updated_at", self.updated_at),
        ):
            _require_utc(value, name)
        if self.started_at is not None:
            _require_utc(self.started_at, "started_at")
        if self.finished_at is not None:
            _require_utc(self.finished_at, "finished_at")

        terminal_values = sum(
            value is not None for value in (self.result, self.failure, self.cancellation_reason)
        )
        if self.state is ExecutionState.COMPLETED:
            if self.result is None or terminal_values != 1 or self.finished_at is None:
                raise ValueError("COMPLETED requires exactly one result and finished_at")
        elif self.state is ExecutionState.FAILED:
            if self.failure is None or terminal_values != 1 or self.finished_at is None:
                raise ValueError("FAILED requires exactly one failure and finished_at")
        elif self.state is ExecutionState.CANCELLED:
            if self.cancellation_reason is None or terminal_values != 1 or self.finished_at is None:
                raise ValueError("CANCELLED requires exactly one cancellation reason and finished_at")
        elif terminal_values != 0 or self.finished_at is not None:
            raise ValueError("non-terminal executions cannot contain terminal data")

    @classmethod
    def create(
        cls,
        *,
        execution_id: ExecutionId,
        ownership: Ownership,
        agent_id: AgentId,
        task: TaskSnapshot,
        correlation_id: CorrelationId,
        limits: ExecutionLimits,
        now: datetime,
        causation_id: EventId | CommandId | None = None,
        parent_execution_id: ExecutionId | None = None,
        agent_config_version: int | None = None,
    ) -> Execution:
        return cls(
            execution_id=execution_id,
            ownership=ownership,
            agent_id=agent_id,
            task=task,
            state=ExecutionState.QUEUED,
            state_version=Version(1),
            correlation_id=correlation_id,
            causation_id=causation_id,
            parent_execution_id=parent_execution_id,
            context_manifest_ref=None,
            result=None,
            failure=None,
            cancellation_reason=None,
            limits=limits,
            usage=ExecutionUsage(),
            iteration_count=0,
            created_at=now,
            queued_at=now,
            started_at=None,
            updated_at=now,
            finished_at=None,
            agent_config_version=agent_config_version,
        )
