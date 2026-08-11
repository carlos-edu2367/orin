from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NewType

from agentos.execution.models import (
    CancellationReason,
    CheckpointReference,
    CorrelationId,
    ExecutionId,
    ExecutionState,
    TaskReference,
    UserId,
    WorkspaceId,
)

ActorRef = NewType("ActorRef", str)
WorkerRef = NewType("WorkerRef", str)
ModelRequirementsReference = NewType("ModelRequirementsReference", str)
ModelSelectionReference = NewType("ModelSelectionReference", str)
ApprovedRequirementsReference = NewType("ApprovedRequirementsReference", str)
ContextReference = NewType("ContextReference", str)
ActionReference = NewType("ActionReference", str)
InvocationReference = NewType("InvocationReference", str)
InputRequestReference = NewType("InputRequestReference", str)
ResultReference = NewType("RuntimeResultReference", str)
DiagnosticReference = NewType("RuntimeDiagnosticReference", str)


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")


def _non_negative(value: int | Decimal, name: str) -> None:
    if value < 0:
        raise ValueError(f"{name} cannot be negative")


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    execution_id: ExecutionId
    user_id: UserId
    workspace_id: WorkspaceId | None
    agent_id: str
    actor_ref: ActorRef
    worker_ref: WorkerRef
    correlation_id: CorrelationId
    purpose: str
    model_requirements_ref: ModelRequirementsReference
    resume_from: CheckpointReference | None = None
    requested_at: datetime | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("execution_id", self.execution_id),
            ("user_id", self.user_id),
            ("agent_id", self.agent_id),
            ("actor_ref", self.actor_ref),
            ("worker_ref", self.worker_ref),
            ("correlation_id", self.correlation_id),
            ("purpose", self.purpose),
            ("model_requirements_ref", self.model_requirements_ref),
        ):
            _required(value, name)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        if self.resume_from is not None:
            _required(self.resume_from, "resume_from")
        if self.requested_at is not None and (
            self.requested_at.tzinfo is None or self.requested_at.utcoffset() is None
        ):
            raise ValueError("requested_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class RuntimeUsage:
    duration_seconds: int = 0
    iterations: int = 0
    provider_tokens: int = 0
    cost: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for name, value in (
            ("duration_seconds", self.duration_seconds),
            ("iterations", self.iterations),
            ("provider_tokens", self.provider_tokens),
            ("cost", self.cost),
        ):
            _non_negative(value, name)

    def plus(self, other: RuntimeUsage) -> RuntimeUsage:
        return RuntimeUsage(
            duration_seconds=self.duration_seconds + other.duration_seconds,
            iterations=self.iterations + other.iterations,
            provider_tokens=self.provider_tokens + other.provider_tokens,
            cost=self.cost + other.cost,
        )


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    max_duration_seconds: int | None = None
    max_iterations: int | None = None
    provider_timeout_seconds: int | None = None
    action_timeout_seconds: int | None = None
    max_cost: Decimal | None = None
    max_provider_tokens: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("max_duration_seconds", self.max_duration_seconds),
            ("max_iterations", self.max_iterations),
            ("provider_timeout_seconds", self.provider_timeout_seconds),
            ("action_timeout_seconds", self.action_timeout_seconds),
            ("max_provider_tokens", self.max_provider_tokens),
        ):
            if value is not None:
                _non_negative(value, name)
        if self.max_cost is not None:
            _non_negative(self.max_cost, "max_cost")


class RuntimeErrorCategory(StrEnum):
    INITIALIZATION = "INITIALIZATION"
    CONTEXT = "CONTEXT"
    MODEL_RESOLUTION = "MODEL_RESOLUTION"
    PROVIDER = "PROVIDER"
    ACTION = "ACTION"
    CHECKPOINT = "CHECKPOINT"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    ACTION_TIMEOUT = "ACTION_TIMEOUT"
    USER_WAIT_TIMEOUT = "USER_WAIT_TIMEOUT"
    LIMIT = "LIMIT"
    CANCELLATION = "CANCELLATION"
    CONCURRENCY = "CONCURRENCY"
    RECONCILIATION = "RECONCILIATION"


class Retryability(StrEnum):
    NEVER = "NEVER"
    SAFE = "SAFE"
    POLICY_DEPENDENT = "POLICY_DEPENDENT"


@dataclass(frozen=True, slots=True)
class RuntimeErrorInfo:
    category: RuntimeErrorCategory
    code: str
    retryability: Retryability = Retryability.NEVER
    detail_ref: DiagnosticReference | None = None

    def __post_init__(self) -> None:
        _required(self.code, "code")
        if self.detail_ref is not None:
            _required(self.detail_ref, "detail_ref")


@dataclass(frozen=True, slots=True)
class CompletedOutcome:
    execution_id: ExecutionId
    result_ref: ResultReference
    usage: RuntimeUsage


@dataclass(frozen=True, slots=True)
class WaitingOutcome:
    execution_id: ExecutionId
    state: ExecutionState

    def __post_init__(self) -> None:
        if self.state not in {ExecutionState.WAITING_USER, ExecutionState.PAUSED}:
            raise ValueError("WaitingOutcome requires WAITING_USER or PAUSED")


@dataclass(frozen=True, slots=True)
class FailedOutcome:
    execution_id: ExecutionId
    error: RuntimeErrorInfo


@dataclass(frozen=True, slots=True)
class CancelledOutcome:
    execution_id: ExecutionId
    reason: CancellationReason


RuntimeOutcome = CompletedOutcome | WaitingOutcome | FailedOutcome | CancelledOutcome


@dataclass(frozen=True, slots=True)
class OperationContext:
    user_id: UserId
    workspace_id: WorkspaceId | None
    agent_id: str
    execution_id: ExecutionId
    correlation_id: CorrelationId
    purpose: str
    actor_ref: ActorRef

    def __post_init__(self) -> None:
        for name, value in (
            ("user_id", self.user_id),
            ("agent_id", self.agent_id),
            ("execution_id", self.execution_id),
            ("correlation_id", self.correlation_id),
            ("purpose", self.purpose),
            ("actor_ref", self.actor_ref),
        ):
            _required(value, name)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")


@dataclass(frozen=True, slots=True)
class ContextAssemblyRequest:
    context: OperationContext
    turn: int
    task_ref: TaskReference
    model_requirements_ref: ModelRequirementsReference
    prior_manifest_ref: ContextReference | None = None

    def __post_init__(self) -> None:
        if self.turn < 1:
            raise ValueError("turn must be positive")
        _required(self.task_ref, "task_ref")
        _required(self.model_requirements_ref, "model_requirements_ref")


@dataclass(frozen=True, slots=True)
class ContextTurnUpdate:
    context: OperationContext
    turn: int
    context_ref: ContextReference
    manifest_ref: ContextReference | None = None
    provider_result_ref: ResultReference | None = None
    action_result_ref: ResultReference | None = None


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    context_ref: ContextReference
    manifest_ref: ContextReference


@dataclass(frozen=True, slots=True)
class ModelResolveRequest:
    context: OperationContext
    requirements_ref: ModelRequirementsReference


@dataclass(frozen=True, slots=True)
class ModelSelection:
    selection_ref: ModelSelectionReference
    approved_requirements_ref: ApprovedRequirementsReference
    canonical_selection: object | None = None
    approved_requirements: object | None = None


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    context: OperationContext
    selection: ModelSelection
    context_ref: ContextReference
    invocation_ref: InvocationReference
    limits: RuntimeLimits
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ProviderFinal:
    result_ref: ResultReference
    usage: RuntimeUsage = RuntimeUsage()


@dataclass(frozen=True, slots=True)
class ProviderToolRequest:
    action_ref: ActionReference
    invocation_ref: InvocationReference
    usage: RuntimeUsage = RuntimeUsage()


@dataclass(frozen=True, slots=True)
class ProviderUserInputRequest:
    input_request_ref: InputRequestReference
    usage: RuntimeUsage = RuntimeUsage()


@dataclass(frozen=True, slots=True)
class ProviderFailed:
    error: RuntimeErrorInfo
    usage: RuntimeUsage = RuntimeUsage()


@dataclass(frozen=True, slots=True)
class ProviderCancelled:
    reason: CancellationReason
    usage: RuntimeUsage = RuntimeUsage()


@dataclass(frozen=True, slots=True)
class ProviderIndeterminate:
    error: RuntimeErrorInfo
    usage: RuntimeUsage = RuntimeUsage()


ProviderOutcome = ProviderFinal | ProviderToolRequest | ProviderUserInputRequest | ProviderFailed | ProviderCancelled | ProviderIndeterminate


@dataclass(frozen=True, slots=True)
class ActionRequest:
    context: OperationContext
    action_ref: ActionReference
    invocation_ref: InvocationReference
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ActionSucceeded:
    result_ref: ResultReference
    usage: RuntimeUsage = RuntimeUsage()


@dataclass(frozen=True, slots=True)
class ActionFailed:
    error: RuntimeErrorInfo
    usage: RuntimeUsage = RuntimeUsage()


@dataclass(frozen=True, slots=True)
class ActionCancelled:
    reason: CancellationReason
    usage: RuntimeUsage = RuntimeUsage()


ActionOutcome = ActionSucceeded | ActionFailed | ActionCancelled


@dataclass(frozen=True, slots=True)
class CheckpointSnapshot:
    checkpoint_ref: CheckpointReference
    execution_id: ExecutionId
    state_version: int
    iteration: int
    context_manifest_ref: ContextReference
    pending_action_ref: ActionReference | None = None


class BudgetDecision(StrEnum):
    CONTINUE = "CONTINUE"
    PAUSE = "PAUSE"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class BudgetEvaluation:
    decision: BudgetDecision
    error: RuntimeErrorInfo | None = None


@dataclass(frozen=True, slots=True)
class BudgetRequest:
    context: OperationContext
    limits: RuntimeLimits
    usage: RuntimeUsage
    effect: str


__all__ = [
    "ActionCancelled",
    "ActionFailed",
    "ActionOutcome",
    "ActionRequest",
    "ActionSucceeded",
    "ActorRef",
    "ApprovedRequirementsReference",
    "ContextAssemblyRequest",
    "ContextReference",
    "ContextSnapshot",
    "ContextTurnUpdate",
    "CompletedOutcome",
    "DiagnosticReference",
    "InputRequestReference",
    "InvocationReference",
    "ModelRequirementsReference",
    "ModelResolveRequest",
    "ModelSelection",
    "ModelSelectionReference",
    "OperationContext",
    "ProviderCancelled",
    "ProviderFailed",
    "ProviderIndeterminate",
    "ProviderFinal",
    "ProviderOutcome",
    "ProviderRequest",
    "ProviderToolRequest",
    "ProviderUserInputRequest",
    "ResultReference",
    "Retryability",
    "RuntimeErrorCategory",
    "RuntimeErrorInfo",
    "RuntimeLimits",
    "RuntimeOutcome",
    "RuntimeRequest",
    "RuntimeUsage",
    "WaitingOutcome",
    "FailedOutcome",
    "CancelledOutcome",
    "WorkerRef",
    "CheckpointSnapshot",
    "BudgetDecision",
    "BudgetEvaluation",
    "BudgetRequest",
]
