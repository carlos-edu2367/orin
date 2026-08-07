from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from threading import RLock
from typing import Protocol

from .models import (
    CapabilityCheckpoint,
    CapabilityDescriptor,
    CapabilityEventType,
    CapabilityOperationContext,
    CapabilityProgram,
    CapabilityRef,
    CapabilityRun,
    CapabilityRunState,
    CapabilityStep,
    CapabilityStepId,
    ChildExecutionState,
    EffectState,
    IdempotencyKey,
    ResourceUsage,
    Retryability,
    StructuredValue,
    ToolRef,
)


@dataclass(frozen=True, slots=True)
class ToolLimitRequest:
    timeout_seconds: int
    maximum_resource_usage: int


@dataclass(frozen=True, slots=True)
class CapabilityToolInvocation:
    capability_run_id: str
    step_id: CapabilityStepId
    invocation_id: str
    tool_ref: ToolRef
    context: CapabilityOperationContext
    arguments: StructuredValue
    idempotency_key: IdempotencyKey | str | None
    limits: ToolLimitRequest


@dataclass(frozen=True, slots=True)
class ToolSucceeded:
    invocation_id: str
    result_ref: str
    usage: ResourceUsage = ResourceUsage()
    effect_state: EffectState = EffectState.APPLIED


@dataclass(frozen=True, slots=True)
class ToolFailed:
    invocation_id: str
    error_code: str
    retryability: Retryability = Retryability.NEVER
    effect_state: EffectState = EffectState.NOT_APPLIED
    result_ref: str | None = None
    usage: ResourceUsage = ResourceUsage()


@dataclass(frozen=True, slots=True)
class ToolCancelled:
    invocation_id: str
    reason: str
    partial_result_ref: str | None
    effect_state: EffectState = EffectState.UNKNOWN
    usage: ResourceUsage = ResourceUsage()


@dataclass(frozen=True, slots=True)
class ToolWaiting:
    invocation_id: str
    reason: str = "tool waiting"
    usage: ResourceUsage = ResourceUsage()


ToolInvocationOutcome = ToolSucceeded | ToolFailed | ToolCancelled | ToolWaiting


@dataclass(frozen=True, slots=True)
class CapabilityToolCancel:
    capability_run_id: str
    step_id: CapabilityStepId
    context: CapabilityOperationContext
    reason: str


class CapabilityToolPort(Protocol):
    def invoke(self, request: CapabilityToolInvocation) -> ToolInvocationOutcome: ...

    def request_cancel(self, request: CapabilityToolCancel) -> object: ...

    def reconcile(self, request: CapabilityToolInvocation) -> ToolInvocationOutcome: ...


@dataclass(frozen=True, slots=True)
class ChildExecutionContext:
    user_id: str
    workspace_id: str | None
    agent_id: str
    parent_execution_id: str
    correlation_id: str
    purpose: str
    actor: str


@dataclass(frozen=True, slots=True)
class CreateChildExecution:
    capability_run_id: str
    step_id: CapabilityStepId
    child_capability_ref: CapabilityRef
    context: ChildExecutionContext
    input_refs: tuple[str, ...]
    purpose: str
    causation_ref: str
    maximum_depth: int


@dataclass(frozen=True, slots=True)
class ChildExecutionSnapshot:
    execution_id: str
    state: ChildExecutionState
    result_ref: str | None


@dataclass(frozen=True, slots=True)
class AuthorizedChildExecutionQuery:
    execution_id: str
    capability_run_id: str
    context: CapabilityOperationContext


@dataclass(frozen=True, slots=True)
class CancelChildExecution:
    execution_id: str
    capability_run_id: str
    context: CapabilityOperationContext
    reason: str


class ChildExecutionPort(Protocol):
    def create(self, request: CreateChildExecution) -> str: ...

    def inspect(self, query: AuthorizedChildExecutionQuery) -> ChildExecutionSnapshot: ...

    def request_cancel(self, request: CancelChildExecution) -> object: ...


@dataclass(frozen=True, slots=True)
class CapabilityEvent:
    event_type: CapabilityEventType
    capability_run_id: str
    capability_ref: CapabilityRef
    execution_id: str
    correlation_id: str
    sequence: int
    occurred_at: datetime
    step_id: str | None = None
    result_ref: str | None = None
    outcome: str | None = None
    reason: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    purpose: str | None = None
    state_version: int | None = None
    usage: ResourceUsage | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("event sequence must be positive")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("event occurred_at must be timezone-aware")
        for name in ("capability_run_id", "execution_id", "correlation_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-blank")
        for name in ("step_id", "result_ref", "outcome", "reason"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 255):
                raise ValueError(f"{name} must be bounded when supplied")
        if self.state_version is not None and self.state_version < 1:
            raise ValueError("state_version must be positive when supplied")


@dataclass(frozen=True, slots=True)
class CapabilityStateWrite:
    run: CapabilityRun
    checkpoint: CapabilityCheckpoint | None
    event: CapabilityEvent | None


class StateConflict(RuntimeError):
    """Optimistic state version conflict."""


class CapabilityStateNotFound(LookupError):
    """The scoped run or checkpoint is not visible."""


class CapabilityStatePort(Protocol):
    def create(self, run: CapabilityRun, *, start_key: tuple[str, ...]) -> CapabilityRun: ...

    def find_start(self, start_key: tuple[str, ...]) -> CapabilityRun | None: ...

    def load(self, capability_run_id: str, context: CapabilityOperationContext) -> CapabilityRun: ...

    def save(
        self,
        run: CapabilityRun,
        *,
        expected_version: int,
        checkpoint: CapabilityCheckpoint | None = None,
        event: CapabilityEvent | None = None,
    ) -> CapabilityRun: ...

    def events(self) -> tuple[CapabilityEvent, ...]: ...

    def load_checkpoint(self, checkpoint_ref: str, context: CapabilityOperationContext) -> CapabilityCheckpoint: ...


class CapabilityClock(Protocol):
    def __call__(self) -> datetime: ...


class CapabilityAuthorizationPort(Protocol):
    def authorize(
        self,
        context: CapabilityOperationContext,
        descriptor: CapabilityDescriptor,
        step: CapabilityStep,
        arguments: StructuredValue,
    ) -> bool: ...


class DefaultCapabilityAuthorization:
    def authorize(self, context, descriptor, step, arguments) -> bool:
        allowed = set(descriptor.permissions)
        return all(permission in allowed for permission in step.authorization) and (
            step.tool_ref is None or step.tool_ref in descriptor.allowed_tools
        ) and (
            step.child_capability_ref is None or step.child_capability_ref in descriptor.allowed_child_capabilities
        )


@dataclass(frozen=True, slots=True)
class CancelCapabilityResult:
    cancelled: bool
    state_version: int


class InMemoryCapabilityState:
    """Reference-only state facade with optimistic versions and bounded outbox records."""

    def __init__(self) -> None:
        self._runs: dict[str, CapabilityRun] = {}
        self._starts: dict[tuple[str, ...], str] = {}
        self._outbox: list[CapabilityEvent] = []
        self._checkpoints: dict[str, CapabilityCheckpoint] = {}
        self._lock = RLock()

    def create(self, run: CapabilityRun, *, start_key: tuple[str, ...]) -> CapabilityRun:
        with self._lock:
            existing_id = self._starts.get(start_key)
            if existing_id is not None:
                return self._runs[existing_id]
            self._runs[str(run.capability_run_id)] = run
            self._starts[start_key] = str(run.capability_run_id)
            return run

    def find_start(self, start_key: tuple[str, ...]) -> CapabilityRun | None:
        with self._lock:
            run_id = self._starts.get(start_key)
            return self._runs.get(run_id) if run_id is not None else None

    def load(self, capability_run_id: str, context: CapabilityOperationContext) -> CapabilityRun:
        with self._lock:
            run = self._runs.get(str(capability_run_id))
            if run is None or not run.context.matches(context):
                raise CapabilityStateNotFound("capability run is not available in this scope")
            return run

    def save(self, run, *, expected_version, checkpoint=None, event=None):
        with self._lock:
            current = self._runs.get(str(run.capability_run_id))
            if current is None:
                raise CapabilityStateNotFound("capability run does not exist")
            if current.state_version != expected_version:
                raise StateConflict(f"expected state version {expected_version}, current {current.state_version}")
            saved = replace(run, state_version=expected_version + 1)
            self._runs[str(run.capability_run_id)] = saved
            if checkpoint is not None:
                self._checkpoints[str(checkpoint.checkpoint_ref)] = checkpoint
            if event is not None:
                self._outbox.append(event)
            return saved

    def events(self) -> tuple[CapabilityEvent, ...]:
        with self._lock:
            return tuple(self._outbox)

    def load_checkpoint(self, checkpoint_ref: str, context: CapabilityOperationContext) -> CapabilityCheckpoint:
        with self._lock:
            checkpoint = self._checkpoints.get(str(checkpoint_ref))
            if checkpoint is None or str(checkpoint.capability_run_id) not in self._runs:
                raise CapabilityStateNotFound("checkpoint is not available")
            run = self._runs[str(checkpoint.capability_run_id)]
            if not run.context.matches(context):
                raise CapabilityStateNotFound("checkpoint is outside the caller scope")
            return checkpoint


__all__ = [name for name in globals() if not name.startswith("_")]
