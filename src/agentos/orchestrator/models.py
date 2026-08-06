from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NewType

from agentos.events.models import CommitState, DataClassification
from agentos.execution.models import (
    AgentId,
    CorrelationId,
    ExecutionId,
    ExecutionLimits,
    ExecutionState,
    Ownership,
    TaskSnapshot,
    UserId,
    Version,
    WorkspaceId,
)


PlanId = NewType("PlanId", str)
WorkId = NewType("WorkId", str)
TriggerId = NewType("TriggerId", str)
Purpose = NewType("Purpose", str)

MAX_TEXT = 128
MAX_REFERENCE = 255
MAX_NODES = 128
MAX_EDGES = 512


def _text(value: object, field_name: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} is invalid")
    return value


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class ProcessingClass(StrEnum):
    STANDARD = "STANDARD"
    ADMINISTRATIVE = "ADMINISTRATIVE"
    RECOVERY = "RECOVERY"


class DependencyCondition(StrEnum):
    COMPLETED = "COMPLETED"
    TERMINAL = "TERMINAL"
    RESULT_MATCHED = "RESULT_MATCHED"


class DependencyFailurePolicy(StrEnum):
    DO_NOT_MATERIALIZE = "DO_NOT_MATERIALIZE"
    MATERIALIZE_FAILURE_HANDLER = "MATERIALIZE_FAILURE_HANDLER"
    CANCEL_RELATED = "CANCEL_RELATED"


class CancellationPropagationPolicy(StrEnum):
    CANCEL_DESCENDANTS = "CANCEL_DESCENDANTS"
    CANCEL_ONLY_TARGET = "CANCEL_ONLY_TARGET"
    DETACH_AUTHORIZED_DESCENDANTS = "DETACH_AUTHORIZED_DESCENDANTS"


class FailurePropagationPolicy(StrEnum):
    STOP_RELATED = "STOP_RELATED"
    CONTINUE_INDEPENDENT = "CONTINUE_INDEPENDENT"


class EvaluationTriggerKind(StrEnum):
    SUBMISSION = "SUBMISSION"
    DEPENDENCY_EVENT = "DEPENDENCY_EVENT"
    SCHEDULED = "SCHEDULED"
    RECOVERY = "RECOVERY"
    MANUAL = "MANUAL"


class PlanStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    EXPIRED = "EXPIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MaterializationStatus(StrEnum):
    MATERIALIZED = "MATERIALIZED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class OpaqueReference:
    value: str

    def __post_init__(self) -> None:
        _text(self.value, "reference", MAX_REFERENCE)
        normalized = self.value.lower().replace("-", "_")
        protected = ("secret", "credential", "access_token", "refresh_token", "prompt_text", "raw_input", "raw_output")
        if any(part in normalized for part in protected):
            raise ValueError("reference is not permitted")
        if any(char.isspace() for char in self.value):
            raise ValueError("reference is not permitted")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return "OpaqueReference(<opaque>)"


@dataclass(frozen=True, slots=True)
class ScheduleConstraint:
    not_before: datetime
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _aware(self.not_before, "not_before")
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")
            if self.expires_at <= self.not_before:
                raise ValueError("expires_at must be after not_before")


@dataclass(frozen=True, slots=True)
class OrchestrationPolicy:
    cancellation_policy: CancellationPropagationPolicy = CancellationPropagationPolicy.CANCEL_DESCENDANTS
    failure_policy: FailurePropagationPolicy = FailurePropagationPolicy.CONTINUE_INDEPENDENT
    maximum_parallel_executions: int = 1
    retry_policy_ref: OpaqueReference | None = None
    context_sharing_policy_ref: OpaqueReference | None = None
    maximum_retries: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "cancellation_policy", CancellationPropagationPolicy(self.cancellation_policy))
        object.__setattr__(self, "failure_policy", FailurePropagationPolicy(self.failure_policy))
        if self.maximum_parallel_executions < 1 or self.maximum_parallel_executions > MAX_NODES:
            raise ValueError("maximum_parallel_executions is invalid")
        if self.maximum_retries < 0 or self.maximum_retries > MAX_NODES:
            raise ValueError("maximum_retries is invalid")


@dataclass(frozen=True, slots=True)
class PlannedWork:
    work_id: WorkId | str
    agent_id: AgentId | str
    task: TaskSnapshot
    limits: ExecutionLimits
    idempotency_key: str
    purpose: Purpose | str
    classification: DataClassification
    schedule: ScheduleConstraint | None = None
    deadline_at: datetime | None = None
    failure_handler_work_id: WorkId | str | None = None

    def __post_init__(self) -> None:
        _text(self.work_id, "work_id")
        _text(self.agent_id, "agent_id")
        _text(self.idempotency_key, "idempotency_key", 256)
        _text(self.purpose, "purpose")
        object.__setattr__(self, "classification", DataClassification(self.classification))
        if self.deadline_at is not None:
            _aware(self.deadline_at, "deadline_at")
        if self.failure_handler_work_id is not None:
            _text(self.failure_handler_work_id, "failure_handler_work_id")


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    predecessor_work_id: WorkId | str
    successor_work_id: WorkId | str
    condition: DependencyCondition
    failure_policy: DependencyFailurePolicy
    result_ref: OpaqueReference | None = None

    def __post_init__(self) -> None:
        _text(self.predecessor_work_id, "predecessor_work_id")
        _text(self.successor_work_id, "successor_work_id")
        object.__setattr__(self, "condition", DependencyCondition(self.condition))
        object.__setattr__(self, "failure_policy", DependencyFailurePolicy(self.failure_policy))
        if self.result_ref is not None and not isinstance(self.result_ref, OpaqueReference):
            raise ValueError("result_ref must be opaque")


@dataclass(frozen=True, slots=True)
class OrchestrationPlanDraft:
    user_id: UserId | str
    workspace_id: WorkspaceId | str | None
    actor: str
    correlation_id: CorrelationId | str
    purpose: Purpose | str
    classification: DataClassification
    nodes: tuple[PlannedWork, ...]
    dependencies: tuple[DependencyEdge, ...]
    policy: OrchestrationPolicy
    created_at: datetime

    def __post_init__(self) -> None:
        _text(self.user_id, "user_id")
        if self.workspace_id is not None:
            _text(self.workspace_id, "workspace_id")
        _text(self.actor, "actor")
        _text(self.correlation_id, "correlation_id")
        _text(self.purpose, "purpose")
        object.__setattr__(self, "classification", DataClassification(self.classification))
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        _aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class OrchestrationPlan:
    plan_id: PlanId | str
    version: Version | int
    user_id: UserId | str
    workspace_id: WorkspaceId | str | None
    actor: str
    correlation_id: CorrelationId | str
    purpose: Purpose | str
    classification: DataClassification
    nodes: tuple[PlannedWork, ...]
    dependencies: tuple[DependencyEdge, ...]
    policy: OrchestrationPolicy
    created_at: datetime
    status: PlanStatus = PlanStatus.ACTIVE

    def __post_init__(self) -> None:
        _text(self.plan_id, "plan_id")
        if self.version < 1:
            raise ValueError("version must be positive")
        draft = OrchestrationPlanDraft(
            user_id=self.user_id,
            workspace_id=self.workspace_id,
            actor=self.actor,
            correlation_id=self.correlation_id,
            purpose=self.purpose,
            classification=self.classification,
            nodes=self.nodes,
            dependencies=self.dependencies,
            policy=self.policy,
            created_at=self.created_at,
        )
        object.__setattr__(self, "classification", draft.classification)
        object.__setattr__(self, "nodes", draft.nodes)
        object.__setattr__(self, "dependencies", draft.dependencies)
        object.__setattr__(self, "status", PlanStatus(self.status))

    @classmethod
    def from_draft(cls, plan_id: PlanId | str, draft: OrchestrationPlanDraft, *, version: int = 1) -> "OrchestrationPlan":
        return cls(
            plan_id=plan_id,
            version=version,
            user_id=draft.user_id,
            workspace_id=draft.workspace_id,
            actor=draft.actor,
            correlation_id=draft.correlation_id,
            purpose=draft.purpose,
            classification=draft.classification,
            nodes=draft.nodes,
            dependencies=draft.dependencies,
            policy=draft.policy,
            created_at=draft.created_at,
        )


@dataclass(frozen=True, slots=True)
class RunAgentTask:
    agent_id: AgentId | str
    task: TaskSnapshot
    limits: ExecutionLimits


@dataclass(frozen=True, slots=True)
class ExecutePlan:
    plan: OrchestrationPlanDraft


@dataclass(frozen=True, slots=True)
class ContinueExecution:
    execution_id: ExecutionId | str
    input_ref: OpaqueReference


@dataclass(frozen=True, slots=True)
class AdministerAgent:
    operation: object


OrchestrationIntent = RunAgentTask | ExecutePlan | ContinueExecution | AdministerAgent


@dataclass(frozen=True, slots=True)
class OrchestrationRequest:
    actor: str
    user_id: UserId | str
    workspace_id: WorkspaceId | str | None
    intent: OrchestrationIntent
    correlation_id: CorrelationId | str
    purpose: Purpose | str
    idempotency_key: str
    requested_at: datetime
    causation_id: str | None = None
    classification: DataClassification = DataClassification.INTERNAL

    def __post_init__(self) -> None:
        for name in ("actor", "user_id", "correlation_id", "purpose", "idempotency_key"):
            _text(getattr(self, name), name, 256 if name == "idempotency_key" else MAX_TEXT)
        if self.workspace_id is not None:
            _text(self.workspace_id, "workspace_id")
        if self.causation_id is not None:
            _text(self.causation_id, "causation_id", 256)
        _aware(self.requested_at, "requested_at")
        object.__setattr__(self, "classification", DataClassification(self.classification))


@dataclass(frozen=True, slots=True)
class EvaluationTrigger:
    kind: EvaluationTriggerKind
    requested_at: datetime
    cause_ref: OpaqueReference | None = None
    actor: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    purpose: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", EvaluationTriggerKind(self.kind))
        _aware(self.requested_at, "requested_at")
        for name in ("actor", "user_id", "purpose", "correlation_id"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        if self.workspace_id is not None:
            _text(self.workspace_id, "workspace_id")


@dataclass(frozen=True, slots=True)
class CancelOrchestration:
    actor: str
    user_id: UserId | str
    workspace_id: WorkspaceId | str | None
    plan_id: PlanId | str
    policy: CancellationPropagationPolicy
    correlation_id: CorrelationId | str
    purpose: Purpose | str
    idempotency_key: str
    requested_at: datetime
    expected_version: Version | int | None = None

    def __post_init__(self) -> None:
        for name in ("actor", "user_id", "plan_id", "correlation_id", "purpose", "idempotency_key"):
            _text(getattr(self, name), name, 256 if name == "idempotency_key" else MAX_TEXT)
        if self.workspace_id is not None:
            _text(self.workspace_id, "workspace_id")
        object.__setattr__(self, "policy", CancellationPropagationPolicy(self.policy))
        _aware(self.requested_at, "requested_at")
        if self.expected_version is not None and self.expected_version < 1:
            raise ValueError("expected_version must be positive")


@dataclass(frozen=True, slots=True)
class RetryExecution:
    actor: str
    user_id: UserId | str
    workspace_id: WorkspaceId | str | None
    plan_id: PlanId | str
    work_id: WorkId | str
    previous_execution_id: ExecutionId | str
    correlation_id: CorrelationId | str
    purpose: Purpose | str
    idempotency_key: str
    requested_at: datetime
    expected_plan_version: Version | int

    def __post_init__(self) -> None:
        for name in ("actor", "user_id", "plan_id", "work_id", "previous_execution_id", "correlation_id", "purpose", "idempotency_key"):
            _text(getattr(self, name), name, 256 if name == "idempotency_key" else MAX_TEXT)
        if self.workspace_id is not None:
            _text(self.workspace_id, "workspace_id")
        _aware(self.requested_at, "requested_at")
        if self.expected_plan_version < 1:
            raise ValueError("expected_plan_version must be positive")


@dataclass(frozen=True, slots=True)
class CreateExecutionRequest:
    ownership: Ownership
    agent_id: AgentId | str
    agent_config_version: int
    task: TaskSnapshot
    limits: ExecutionLimits
    correlation_id: CorrelationId | str
    purpose: Purpose | str
    idempotency_key: str
    requested_at: datetime
    causation_id: str | None = None
    parent_execution_id: ExecutionId | str | None = None
    processing_class: ProcessingClass = ProcessingClass.STANDARD

    def __post_init__(self) -> None:
        _text(self.agent_id, "agent_id")
        if self.agent_config_version < 1:
            raise ValueError("agent_config_version must be positive")
        _text(self.correlation_id, "correlation_id")
        _text(self.purpose, "purpose")
        _text(self.idempotency_key, "idempotency_key", 256)
        _aware(self.requested_at, "requested_at")
        object.__setattr__(self, "processing_class", ProcessingClass(self.processing_class))


@dataclass(frozen=True, slots=True)
class ExecutionCreationReceipt:
    execution_id: ExecutionId | str
    state_version: Version | int
    transaction_id: str | None
    commit_state: CommitState
    already_applied: bool = False

    def __post_init__(self) -> None:
        _text(self.execution_id, "execution_id")
        if self.state_version < 1:
            raise ValueError("state_version must be positive")
        object.__setattr__(self, "commit_state", CommitState(self.commit_state))


@dataclass(frozen=True, slots=True)
class OrchestrationReceipt:
    plan_id: PlanId | str
    plan_version: Version | int
    transaction_id: str
    idempotency_key: str
    commit_state: CommitState
    status: str = "ACCEPTED"

    def __post_init__(self) -> None:
        _text(self.plan_id, "plan_id")
        _text(self.transaction_id, "transaction_id", 256)
        _text(self.idempotency_key, "idempotency_key", 256)
        if self.plan_version < 1:
            raise ValueError("plan_version must be positive")
        object.__setattr__(self, "commit_state", CommitState(self.commit_state))


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    execution_id: ExecutionId | str
    expected_state_version: Version | int
    processing_class: ProcessingClass
    correlation_id: CorrelationId | str
    purpose: Purpose | str
    idempotency_key: str

    def __post_init__(self) -> None:
        for name in ("execution_id", "correlation_id", "purpose", "idempotency_key"):
            _text(getattr(self, name), name, 256 if name == "idempotency_key" else MAX_TEXT)
        if self.expected_state_version < 1:
            raise ValueError("expected_state_version must be positive")
        object.__setattr__(self, "processing_class", ProcessingClass(self.processing_class))


@dataclass(frozen=True, slots=True)
class ScheduleTrigger:
    trigger_id: TriggerId | str
    plan_id: PlanId | str
    work_id: WorkId | str
    schedule: ScheduleConstraint
    idempotency_key: str

    def __post_init__(self) -> None:
        for name in ("trigger_id", "plan_id", "work_id", "idempotency_key"):
            _text(getattr(self, name), name, 256 if name == "idempotency_key" else MAX_TEXT)


@dataclass(frozen=True, slots=True)
class SupervisionSnapshot:
    execution_id: ExecutionId | str
    observed_state: ExecutionState
    state_version: Version | int
    last_progress_at: datetime | None = None
    pending_action_ref: OpaqueReference | None = None
    result_ref: OpaqueReference | None = None

    def __post_init__(self) -> None:
        _text(self.execution_id, "execution_id")
        object.__setattr__(self, "observed_state", ExecutionState(self.observed_state))
        if self.state_version < 1:
            raise ValueError("state_version must be positive")
        if self.last_progress_at is not None:
            _aware(self.last_progress_at, "last_progress_at")


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    plan_id: PlanId | str
    plan_version: Version | int
    ready_work_ids: tuple[str, ...] = ()
    materialized_execution_ids: tuple[str, ...] = ()
    expired_work_ids: tuple[str, ...] = ()
    dispatches: tuple[DispatchRequest, ...] = ()
    commit_state: CommitState = CommitState.COMMITTED
    transaction_id: str | None = None


@dataclass(frozen=True, slots=True)
class CancellationReceipt:
    plan_id: PlanId | str
    plan_version: Version | int
    cancelled_execution_ids: tuple[str, ...]
    commit_state: CommitState
    transaction_id: str | None = None


@dataclass(frozen=True, slots=True)
class RetryReceipt:
    plan_id: PlanId | str
    work_id: WorkId | str
    previous_execution_id: ExecutionId | str
    execution_id: ExecutionId | str
    commit_state: CommitState
    transaction_id: str | None = None


__all__ = [
    "AdministerAgent", "CancellationPropagationPolicy", "CancellationReceipt", "ContinueExecution",
    "CreateExecutionRequest", "DependencyCondition", "DependencyEdge", "DependencyFailurePolicy",
    "DispatchRequest", "EvaluationOutcome", "EvaluationTrigger", "EvaluationTriggerKind",
    "ExecutePlan", "ExecutionCreationReceipt", "FailurePropagationPolicy", "MaterializationStatus",
    "OpaqueReference", "OrchestrationPlan", "OrchestrationPlanDraft", "OrchestrationPolicy",
    "OrchestrationReceipt", "OrchestrationRequest", "PlanId", "PlanStatus", "PlannedWork",
    "ProcessingClass", "Purpose", "RetryExecution", "RetryReceipt", "RunAgentTask", "ScheduleConstraint",
    "ScheduleTrigger", "SupervisionSnapshot", "TriggerId", "WorkId",
]
