from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Mapping, NewType

from agentos.execution.models import ExecutionState, TaskSnapshot


MAX_IDENTIFIER = 255
MAX_DESCRIPTION = 1024
MAX_SCHEMA = 255
MAX_BINDINGS = 32
MAX_VALUE_ITEMS = 32
MAX_VALUE_TEXT = 512

CapabilityId = NewType("CapabilityId", str)
CapabilityVersion = NewType("CapabilityVersion", int)
CapabilityRunId = NewType("CapabilityRunId", str)
CapabilityStepId = NewType("CapabilityStepId", str)
CapabilityCheckpointRef = NewType("CapabilityCheckpointRef", str)
CapabilityProgramRef = NewType("CapabilityProgramRef", str)
CapabilityRequestId = NewType("CapabilityRequestId", str)
RegistryRequestId = NewType("RegistryRequestId", str)
InputReference = NewType("InputReference", str)
ResultReference = NewType("CapabilityResultReference", str)
ActorRef = NewType("ActorRef", str)
CorrelationId = NewType("CapabilityCorrelationId", str)
UserId = NewType("CapabilityUserId", str)
WorkspaceId = NewType("CapabilityWorkspaceId", str)
AgentId = NewType("CapabilityAgentId", str)
ExecutionId = NewType("CapabilityExecutionId", str)
IdempotencyKey = NewType("CapabilityIdempotencyKey", str)
IntegrityRef = NewType("IntegrityRef", str)


def _required(value: object, name: str, maximum: int = MAX_IDENTIFIER) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds its public maximum")
    return value


def _positive(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be positive")


def _non_negative(value: int | Decimal, name: str) -> None:
    if isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} cannot be negative")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class CapabilityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    DISABLED = "DISABLED"


class CompensationPolicy(StrEnum):
    NONE = "NONE"
    EXPLICIT_STEPS = "EXPLICIT_STEPS"


class CapabilityCancellationMode(StrEnum):
    COOPERATIVE = "COOPERATIVE"
    COOPERATIVE_WITH_COMPENSATION = "COOPERATIVE_WITH_COMPENSATION"
    IMMEDIATE = "IMMEDIATE"


class CapabilityRunState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_TOOL = "WAITING_TOOL"
    WAITING_CHILD = "WAITING_CHILD"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    COMPENSATING = "COMPENSATING"


class CapabilityStepKind(StrEnum):
    TOOL = "TOOL"
    CHILD_EXECUTION = "CHILD_EXECUTION"
    DECISION = "DECISION"
    CHECKPOINT = "CHECKPOINT"
    COMPENSATION = "COMPENSATION"


class EffectState(StrEnum):
    NOT_APPLIED = "NOT_APPLIED"
    APPLIED = "APPLIED"
    UNKNOWN = "UNKNOWN"


class StepOutcomeState(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    WAITING = "WAITING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class WaitReason(StrEnum):
    TOOL = "TOOL"
    CHILD = "CHILD"
    USER = "USER"
    PAUSE = "PAUSE"


class Retryability(StrEnum):
    NEVER = "NEVER"
    SAFE = "SAFE"
    POLICY_DEPENDENT = "POLICY_DEPENDENT"


class ToolIdempotency(StrEnum):
    IDEMPOTENT = "IDEMPOTENT"
    IDEMPOTENT_WITH_KEY = "IDEMPOTENT_WITH_KEY"
    NON_IDEMPOTENT = "NON_IDEMPOTENT"


class ChildExecutionState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CapabilityEventType(StrEnum):
    STARTED = "CapabilityStarted"
    STEP_STARTED = "CapabilityStepStarted"
    STEP_FINISHED = "CapabilityStepFinished"
    CHECKPOINT_CREATED = "CapabilityCheckpointCreated"
    CHILD_EXECUTION_CREATED = "CapabilityChildExecutionCreated"
    COMPENSATION_FINISHED = "CapabilityCompensationFinished"
    FINISHED = "CapabilityFinished"
    FAILED = "CapabilityFailed"
    CANCELLED = "CapabilityCancelled"


@dataclass(frozen=True, slots=True)
class ToolRef:
    tool_id: str
    version: int

    def __post_init__(self) -> None:
        _required(self.tool_id, "tool_id")
        _positive(self.version, "tool version")


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    capability_id: CapabilityId | str
    version: CapabilityVersion | int

    def __post_init__(self) -> None:
        _required(self.capability_id, "capability_id")
        _positive(self.version, "capability version")
        object.__setattr__(self, "capability_id", CapabilityId(str(self.capability_id)))
        object.__setattr__(self, "version", CapabilityVersion(int(self.version)))


@dataclass(frozen=True, slots=True)
class PermissionRequirement:
    resource_ref: str
    operation: str

    def __post_init__(self) -> None:
        _required(self.resource_ref, "permission resource_ref")
        _required(self.operation, "permission operation")


Permission = PermissionRequirement


@dataclass(frozen=True, slots=True)
class CapabilityLimits:
    timeout_seconds: int
    maximum_steps: int
    maximum_tool_invocations: int
    maximum_child_executions: int
    maximum_parallel_steps: int
    maximum_cost: Decimal | None
    maximum_resource_usage: int

    def __post_init__(self) -> None:
        for name, value in (
            ("timeout_seconds", self.timeout_seconds),
            ("maximum_steps", self.maximum_steps),
            ("maximum_tool_invocations", self.maximum_tool_invocations),
            ("maximum_parallel_steps", self.maximum_parallel_steps),
        ):
            _positive(value, name)
        for name, value in (
            ("maximum_child_executions", self.maximum_child_executions),
            ("maximum_resource_usage", self.maximum_resource_usage),
        ):
            _non_negative(value, name)
        if self.maximum_cost is not None:
            _non_negative(self.maximum_cost, "maximum_cost")

    @property
    def timeout(self) -> int:
        return self.timeout_seconds


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    duration_seconds: int = 0
    tool_invocations: int = 0
    child_executions: int = 0
    steps: int = 0
    cost: Decimal = Decimal("0")
    resource_units: int = 0

    def __post_init__(self) -> None:
        for name, value in (
            ("duration_seconds", self.duration_seconds),
            ("tool_invocations", self.tool_invocations),
            ("child_executions", self.child_executions),
            ("steps", self.steps),
            ("resource_units", self.resource_units),
            ("cost", self.cost),
        ):
            _non_negative(value, name)

    def plus(self, other: ResourceUsage) -> ResourceUsage:
        return ResourceUsage(
            duration_seconds=self.duration_seconds + other.duration_seconds,
            tool_invocations=self.tool_invocations + other.tool_invocations,
            child_executions=self.child_executions + other.child_executions,
            steps=self.steps + other.steps,
            cost=self.cost + other.cost,
            resource_units=self.resource_units + other.resource_units,
        )


_SECRET_FIELDS = {"secret", "token", "password", "cookie", "credential", "handle"}


def _freeze_value(value: object, name: str = "value") -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            _required(value, name, MAX_VALUE_TEXT)
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_VALUE_ITEMS:
            raise ValueError("structured value has too many fields")
        items = []
        for key, item in value.items():
            _required(str(key), "structured field", 64)
            if any(secret in str(key).lower() for secret in _SECRET_FIELDS):
                raise ValueError("structured value contains a secret-like field")
            items.append((str(key), _freeze_value(item, str(key))))
        return tuple(sorted(items))
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_VALUE_ITEMS:
            raise ValueError("structured value has too many items")
        return tuple(_freeze_value(item, name) for item in value)
    raise ValueError("structured value must contain bounded scalar, mapping or sequence data")


@dataclass(frozen=True, slots=True)
class StructuredValue:
    items: tuple[tuple[str, object], ...]

    def __post_init__(self) -> None:
        if len(self.items) > MAX_VALUE_ITEMS:
            raise ValueError("structured value has too many fields")
        normalized = tuple(sorted((str(k), _freeze_value(v, str(k))) for k, v in self.items))
        for key, _ in normalized:
            _required(key, "structured field", 64)
            if any(secret in key.lower() for secret in _SECRET_FIELDS):
                raise ValueError("structured value contains a secret-like field")
        object.__setattr__(self, "items", normalized)

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> StructuredValue:
        return cls(tuple(values.items()))


@dataclass(frozen=True, slots=True)
class InputBinding:
    name: str
    reference: str

    def __post_init__(self) -> None:
        _required(self.name, "binding name", 64)
        _required(self.reference, "binding reference")


@dataclass(frozen=True, slots=True)
class OutputBinding:
    name: str

    def __post_init__(self) -> None:
        _required(self.name, "output binding", 64)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    maximum_attempts: int = 1
    retryable: frozenset[Retryability] = frozenset({Retryability.SAFE})

    def __post_init__(self) -> None:
        _positive(self.maximum_attempts, "maximum_attempts")
        object.__setattr__(self, "retryable", frozenset(Retryability(item) for item in self.retryable))


@dataclass(frozen=True, slots=True)
class CapabilityStep:
    step_id: CapabilityStepId | str
    kind: CapabilityStepKind
    dependencies: tuple[CapabilityStepId | str, ...]
    authorization: tuple[PermissionRequirement, ...]
    timeout_seconds: int
    retry_policy: RetryPolicy
    input_bindings: tuple[InputBinding, ...]
    output_binding: OutputBinding | None = None
    tool_ref: ToolRef | None = None
    child_capability_ref: CapabilityRef | None = None
    declared_branches: tuple[CapabilityStepId | str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.step_id, "step_id")
        _positive(self.timeout_seconds, "step timeout")
        if len(self.dependencies) > MAX_BINDINGS:
            raise ValueError("too many dependencies")
        object.__setattr__(self, "step_id", CapabilityStepId(str(self.step_id)))
        object.__setattr__(self, "dependencies", tuple(CapabilityStepId(str(item)) for item in self.dependencies))
        object.__setattr__(self, "declared_branches", tuple(CapabilityStepId(str(item)) for item in self.declared_branches))
        object.__setattr__(self, "authorization", tuple(self.authorization))
        object.__setattr__(self, "input_bindings", tuple(self.input_bindings))
        if self.kind is CapabilityStepKind.TOOL and self.tool_ref is None:
            raise ValueError("TOOL step requires exact tool_ref")
        if self.kind is CapabilityStepKind.CHILD_EXECUTION and self.child_capability_ref is None:
            raise ValueError("CHILD_EXECUTION step requires child_capability_ref")


@dataclass(frozen=True, slots=True)
class CapabilityProgram:
    steps: tuple[CapabilityStep, ...]
    compensation_steps: tuple[CapabilityStep, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))
        object.__setattr__(self, "compensation_steps", tuple(self.compensation_steps))
        names = [str(step.step_id) for step in self.steps]
        if len(names) != len(set(names)):
            raise ValueError("program contains duplicate step_id")
        known = set(names)
        for step in self.steps:
            if any(str(dep) not in known for dep in step.dependencies):
                raise ValueError("program contains an unknown dependency")
        if len(self.steps) > 256 or len(self.compensation_steps) > 64:
            raise ValueError("program exceeds public step bound")


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    capability_ref: CapabilityRef
    name: str
    description: str
    input_schema: str
    output_schema: str
    allowed_tools: tuple[ToolRef, ...]
    allowed_child_capabilities: tuple[CapabilityRef, ...]
    permissions: tuple[PermissionRequirement, ...]
    limits: CapabilityLimits
    cancellation_policy: CapabilityCancellationMode | str
    compensation_policy: CompensationPolicy
    status: CapabilityStatus

    def __post_init__(self) -> None:
        _required(self.name, "capability name", 128)
        _required(self.description, "capability description", MAX_DESCRIPTION)
        _required(self.input_schema, "input schema", MAX_SCHEMA)
        _required(self.output_schema, "output schema", MAX_SCHEMA)
        object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        object.__setattr__(self, "allowed_child_capabilities", tuple(self.allowed_child_capabilities))
        object.__setattr__(self, "permissions", tuple(self.permissions))
        object.__setattr__(self, "cancellation_policy", CapabilityCancellationMode(self.cancellation_policy))
        object.__setattr__(self, "compensation_policy", CompensationPolicy(self.compensation_policy))
        object.__setattr__(self, "status", CapabilityStatus(self.status))


@dataclass(frozen=True, slots=True)
class CapabilityOperationContext:
    user_id: UserId | str
    workspace_id: WorkspaceId | str | None
    agent_id: AgentId | str
    execution_id: ExecutionId | str
    correlation_id: CorrelationId | str
    purpose: str
    actor: ActorRef | str

    def __post_init__(self) -> None:
        for name in ("user_id", "agent_id", "execution_id", "correlation_id", "actor"):
            _required(getattr(self, name), name)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        _required(self.purpose, "purpose", 128)
        object.__setattr__(self, "actor", ActorRef(str(self.actor)))

    def matches(self, other: CapabilityOperationContext) -> bool:
        return (
            self.user_id == other.user_id
            and self.workspace_id == other.workspace_id
            and self.agent_id == other.agent_id
            and self.execution_id == other.execution_id
            and self.correlation_id == other.correlation_id
            and self.purpose == other.purpose
            and self.actor == other.actor
        )

    def __repr__(self) -> str:
        return (
            "CapabilityOperationContext("
            f"user_id={self.user_id!r}, workspace_id={self.workspace_id!r}, "
            f"agent_id={self.agent_id!r}, execution_id={self.execution_id!r}, "
            f"correlation_id={self.correlation_id!r}, purpose=<bounded>, actor={self.actor!r})"
        )


@dataclass(frozen=True, slots=True)
class CapabilityRegistryOperationContext:
    user_id: UserId | str
    workspace_id: WorkspaceId | str | None
    agent_id: AgentId | str | None
    execution_id: ExecutionId | str | None
    administrative_correlation_id: str | None
    correlation_id: CorrelationId | str
    purpose: str
    actor: ActorRef | str

    def __post_init__(self) -> None:
        _required(self.user_id, "user_id")
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        if self.agent_id is not None:
            _required(self.agent_id, "agent_id")
        if self.execution_id is not None:
            _required(self.execution_id, "execution_id")
        if self.administrative_correlation_id is not None:
            _required(self.administrative_correlation_id, "administrative_correlation_id")
        if (self.execution_id is None) == (self.administrative_correlation_id is None):
            raise ValueError("exactly one execution_id or administrative_correlation_id is required")
        if self.execution_id is not None and self.agent_id is None:
            raise ValueError("agent_id is required for execution-bound registry operations")
        _required(self.correlation_id, "correlation_id")
        _required(self.purpose, "purpose", 128)
        _required(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class AuthorizedCapabilityRegistryQuery:
    context: CapabilityRegistryOperationContext
    capability_ref: CapabilityRef | None = None
    status: tuple[CapabilityStatus, ...] = ()
    permission_filter: tuple[PermissionRequirement, ...] = ()
    page_size: int = 100

    def __post_init__(self) -> None:
        if self.page_size < 1 or self.page_size > 100:
            raise ValueError("page_size must be between 1 and 100")
        object.__setattr__(self, "status", tuple(CapabilityStatus(item) for item in self.status))


@dataclass(frozen=True, slots=True)
class AuthorizedCapabilityQuery:
    context: CapabilityOperationContext
    capability_run_id: CapabilityRunId | str

    def __post_init__(self) -> None:
        _required(self.capability_run_id, "capability_run_id")


@dataclass(frozen=True, slots=True)
class CapabilityStepRecord:
    step_id: CapabilityStepId
    attempt: int
    invocation_id: str | None
    child_execution_id: ExecutionId | str | None
    outcome: StepOutcomeState
    result_ref: ResultReference | str | None
    effect_state: EffectState
    finished_at: datetime
    error_code: str | None = None

    def __post_init__(self) -> None:
        _positive(self.attempt, "step attempt")
        _aware(self.finished_at, "finished_at")
        if self.result_ref is not None:
            _required(self.result_ref, "result_ref")
        if self.error_code is not None:
            _required(self.error_code, "error_code", 128)


@dataclass(frozen=True, slots=True)
class CapabilityCheckpoint:
    checkpoint_ref: CapabilityCheckpointRef | str
    capability_run_id: CapabilityRunId | str
    descriptor_ref: CapabilityRef
    state: CapabilityRunState
    completed_steps: tuple[CapabilityStepRecord, ...]
    current_steps: tuple[CapabilityStepId | str, ...]
    child_execution_ids: tuple[ExecutionId | str, ...]
    usage: ResourceUsage
    next_decision: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        _required(self.checkpoint_ref, "checkpoint_ref")
        _required(self.capability_run_id, "capability_run_id")
        _aware(self.created_at, "checkpoint created_at")
        if self.next_decision is not None:
            _required(self.next_decision, "next_decision", 128)
        object.__setattr__(self, "current_steps", tuple(CapabilityStepId(str(item)) for item in self.current_steps))
        object.__setattr__(self, "child_execution_ids", tuple(str(item) for item in self.child_execution_ids))


@dataclass(frozen=True, slots=True)
class CapabilityRun:
    capability_run_id: CapabilityRunId | str
    capability_ref: CapabilityRef
    context: CapabilityOperationContext
    input_ref: InputReference | str
    state: CapabilityRunState
    state_version: int
    current_steps: tuple[CapabilityStepId | str, ...]
    completed_steps: tuple[CapabilityStepRecord, ...]
    child_execution_ids: tuple[ExecutionId | str, ...]
    usage: ResourceUsage
    checkpoint_ref: CapabilityCheckpointRef | str | None
    result_ref: ResultReference | str | None
    started_at: datetime | None
    finished_at: datetime | None
    cancellation_requested: bool = False

    def __post_init__(self) -> None:
        _required(self.capability_run_id, "capability_run_id")
        _required(self.input_ref, "input_ref")
        _positive(self.state_version, "state_version")
        if self.started_at is not None:
            _aware(self.started_at, "started_at")
        if self.finished_at is not None:
            _aware(self.finished_at, "finished_at")
        if self.state in {CapabilityRunState.SUCCEEDED, CapabilityRunState.FAILED, CapabilityRunState.CANCELLED} and self.finished_at is None:
            raise ValueError("terminal capability run requires finished_at")
        if self.state not in {CapabilityRunState.SUCCEEDED, CapabilityRunState.FAILED, CapabilityRunState.CANCELLED} and self.finished_at is not None:
            raise ValueError("non-terminal capability run cannot have finished_at")
        object.__setattr__(self, "current_steps", tuple(CapabilityStepId(str(item)) for item in self.current_steps))
        object.__setattr__(self, "child_execution_ids", tuple(str(item) for item in self.child_execution_ids))


@dataclass(frozen=True, slots=True)
class RegisterCapability:
    request_id: RegistryRequestId | str
    context: CapabilityRegistryOperationContext
    descriptor: CapabilityDescriptor
    program: CapabilityProgram
    package_integrity_ref: IntegrityRef | str
    idempotency_key: IdempotencyKey | str

    def __post_init__(self) -> None:
        _required(self.request_id, "request_id")
        _required(self.package_integrity_ref, "package_integrity_ref")
        _required(self.idempotency_key, "idempotency_key")


@dataclass(frozen=True, slots=True)
class DisableCapability:
    request_id: RegistryRequestId | str
    context: CapabilityRegistryOperationContext
    capability_ref: CapabilityRef
    expected_status: CapabilityStatus
    reason: str
    idempotency_key: IdempotencyKey | str

    def __post_init__(self) -> None:
        _required(self.request_id, "request_id")
        _required(self.reason, "reason", 128)
        _required(self.idempotency_key, "idempotency_key")
        object.__setattr__(self, "expected_status", CapabilityStatus(self.expected_status))


@dataclass(frozen=True, slots=True)
class StartCapability:
    request_id: CapabilityRequestId | str
    capability_ref: CapabilityRef
    user_id: UserId | str
    workspace_id: WorkspaceId | str | None
    agent_id: AgentId | str
    correlation_id: CorrelationId | str
    purpose: str
    actor: ActorRef | str
    task: TaskSnapshot
    input_ref: InputReference | str
    limits: CapabilityLimits
    idempotency_key: IdempotencyKey | str

    def context(self, execution_id: ExecutionId | str) -> CapabilityOperationContext:
        return CapabilityOperationContext(
            user_id=self.user_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            execution_id=execution_id,
            correlation_id=self.correlation_id,
            purpose=self.purpose,
            actor=self.actor,
        )


@dataclass(frozen=True, slots=True)
class RunCapability:
    capability_run_id: CapabilityRunId | str
    context: CapabilityOperationContext
    expected_state_version: int
    resume_from: CapabilityCheckpointRef | str | None = None

    def __post_init__(self) -> None:
        _required(self.capability_run_id, "capability_run_id")
        _positive(self.expected_state_version, "expected_state_version")


@dataclass(frozen=True, slots=True)
class ResumeCapability(RunCapability):
    """Explicit resume command; it never reopens a terminal run."""


@dataclass(frozen=True, slots=True)
class CancelCapability:
    capability_run_id: CapabilityRunId | str
    context: CapabilityOperationContext
    reason: str
    idempotency_key: IdempotencyKey | str

    def __post_init__(self) -> None:
        _required(self.capability_run_id, "capability_run_id")
        _required(self.reason, "reason", 128)
        _required(self.idempotency_key, "idempotency_key")


@dataclass(frozen=True, slots=True)
class CapabilityAccepted:
    capability_run_id: CapabilityRunId
    execution_id: ExecutionId
    state: CapabilityRunState = CapabilityRunState.QUEUED


@dataclass(frozen=True, slots=True)
class CapabilitySucceeded:
    result_ref: ResultReference | str
    usage: ResourceUsage


@dataclass(frozen=True, slots=True)
class CapabilityWaiting:
    reason: WaitReason
    checkpoint_ref: CapabilityCheckpointRef


@dataclass(frozen=True, slots=True)
class CompensationOutcome:
    completed_steps: tuple[CapabilityStepRecord, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class CapabilityFailed:
    error_code: str
    compensation: CompensationOutcome | None


@dataclass(frozen=True, slots=True)
class CapabilityCancelled:
    reason: str
    compensation: CompensationOutcome | None


CapabilityOutcome = CapabilitySucceeded | CapabilityWaiting | CapabilityFailed | CapabilityCancelled
CapabilityRunSnapshot = CapabilityRun


def execution_state_for_capability_state(state: CapabilityRunState) -> ExecutionState:
    return {
        CapabilityRunState.QUEUED: ExecutionState.QUEUED,
        CapabilityRunState.RUNNING: ExecutionState.RUNNING,
        CapabilityRunState.COMPENSATING: ExecutionState.RUNNING,
        CapabilityRunState.WAITING_TOOL: ExecutionState.WAITING_TOOL,
        CapabilityRunState.WAITING_CHILD: ExecutionState.PAUSED,
        CapabilityRunState.PAUSED: ExecutionState.PAUSED,
        CapabilityRunState.SUCCEEDED: ExecutionState.COMPLETED,
        CapabilityRunState.FAILED: ExecutionState.FAILED,
        CapabilityRunState.CANCELLED: ExecutionState.CANCELLED,
    }[state]


__all__ = [name for name in globals() if not name.startswith("_")]
