from __future__ import annotations

from datetime import datetime
from typing import Protocol

from agentos.execution.models import CancellationReason, Execution, ExecutionId, ExecutionState
from agentos.execution.ports import ControlSignal, ExecutionCommandContext, ExecutionControl
from agentos.providers.ports import ModelResolver as CanonicalModelResolver, ProviderPort as CanonicalProviderPort

from .models import (
    ActionOutcome,
    ActionRequest,
    BudgetEvaluation,
    BudgetRequest,
    CheckpointSnapshot,
    ContextAssemblyRequest,
    ContextSnapshot,
    ContextTurnUpdate,
    ModelResolveRequest,
    ModelSelection,
    ProviderOutcome,
    ProviderRequest,
    RuntimeLimits,
    RuntimeUsage,
)


class ContextManager(Protocol):
    def assemble(self, request: ContextAssemblyRequest) -> ContextSnapshot: ...

    def apply_turn(self, request: ContextTurnUpdate) -> ContextSnapshot: ...

    def finalize(self, execution_id: ExecutionId, disposition: ExecutionState) -> None: ...


class ModelResolver(Protocol):
    def resolve(self, request: ModelResolveRequest) -> ModelSelection: ...


class ProviderPort(Protocol):
    def generate(self, request: ProviderRequest) -> ProviderOutcome: ...


class ToolCapabilityPort(Protocol):
    def invoke(self, request: ActionRequest) -> ActionOutcome: ...


class CheckpointPort(Protocol):
    def load(self, checkpoint_ref: str, context: ExecutionCommandContext) -> CheckpointSnapshot: ...

    def latest_safe(
        self, execution_id: ExecutionId, context: ExecutionCommandContext
    ) -> CheckpointSnapshot | None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class BudgetPolicy(Protocol):
    def evaluate(self, request: BudgetRequest) -> BudgetEvaluation: ...


__all__ = [
    "BudgetPolicy",
    "CheckpointPort",
    "Clock",
    "ContextManager",
    "ControlSignal",
    "ExecutionControl",
    "ExecutionCommandContext",
    "Execution",
    "ModelResolver",
    "ProviderPort",
    "RuntimeLimits",
    "RuntimeUsage",
    "ToolCapabilityPort",
    "CanonicalModelResolver",
    "CanonicalProviderPort",
]
