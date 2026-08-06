from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agentos.events.models import CommitState, DataClassification, EventEnvelope
from agentos.execution.models import ExecutionId, ExecutionState, Version

from .models import (
    CancellationReceipt,
    DispatchRequest,
    EvaluationOutcome,
    OrchestrationPlan,
    OrchestrationReceipt,
    PlanId,
    PlannedWork,
    ScheduleTrigger,
    SupervisionSnapshot,
    WorkId,
)


def _text(value: object, field: str, maximum: int = 256) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} is invalid")


@dataclass(frozen=True, slots=True)
class PlanAccessContext:
    user_id: str
    workspace_id: str | None
    actor: str
    purpose: str
    correlation_id: str
    classification: DataClassification = DataClassification.INTERNAL

    def __post_init__(self) -> None:
        for field in ("user_id", "actor", "purpose", "correlation_id"):
            _text(getattr(self, field), field)
        if self.workspace_id is not None:
            _text(self.workspace_id, "workspace_id")
        object.__setattr__(self, "classification", DataClassification(self.classification))

    def scope_key(self) -> tuple[str, str | None, str]:
        return self.user_id, self.workspace_id, self.actor


@dataclass(frozen=True, slots=True)
class PlanStoreResult:
    receipt: OrchestrationReceipt
    plan: OrchestrationPlan | None = None
    already_applied: bool = False


@dataclass(frozen=True, slots=True)
class MaterializationRecord:
    plan_id: PlanId | str
    plan_version: Version | int
    work_id: WorkId | str
    execution_id: ExecutionId | str
    state_version: Version | int
    idempotency_key: str
    retry_of: ExecutionId | str | None = None


@dataclass(frozen=True, slots=True)
class DispatchReceipt:
    execution_id: ExecutionId | str
    accepted: bool
    already_applied: bool = False


@dataclass(frozen=True, slots=True)
class ScheduleReceipt:
    trigger_id: str
    accepted: bool
    already_applied: bool = False


@dataclass(frozen=True, slots=True)
class SupervisionQuery:
    execution_id: ExecutionId | str
    access: PlanAccessContext


class Orchestrator(Protocol):
    def submit(self, request): ...

    def evaluate(self, plan_id: PlanId | str, trigger): ...

    def request_cancel(self, command) -> CancellationReceipt: ...

    def request_retry(self, command): ...


class ExecutionFactory(Protocol):
    def create(self, request): ...


class SchedulingPort(Protocol):
    def register(self, trigger: ScheduleTrigger) -> ScheduleReceipt: ...

    def cancel(self, trigger_id: str, access: PlanAccessContext) -> bool: ...


class DispatchPort(Protocol):
    def request_dispatch(self, request: DispatchRequest) -> DispatchReceipt: ...


class SupervisionPort(Protocol):
    def observe(self, query: SupervisionQuery) -> SupervisionSnapshot: ...


class PlanStorePort(Protocol):
    def submit(self, plan: OrchestrationPlan, *, access: PlanAccessContext, idempotency_key: str, operation_fingerprint: str) -> PlanStoreResult: ...

    def get(self, plan_id: PlanId | str, access: PlanAccessContext) -> OrchestrationPlan: ...

    def list(self, access: PlanAccessContext) -> tuple[OrchestrationPlan, ...]: ...

    def lookup_idempotency(self, access: PlanAccessContext, idempotency_key: str) -> tuple[str, OrchestrationReceipt] | None: ...

    def inspect_commit(self, *, access: PlanAccessContext, transaction_id: str, idempotency_key: str) -> OrchestrationReceipt: ...

    def materialize(self, *, plan_id: PlanId | str, plan_version: int, work: PlannedWork, execution_id: ExecutionId | str, state_version: int, access: PlanAccessContext, idempotency_key: str, retry_of: ExecutionId | str | None = None) -> PlanStoreResult: ...

    def materialization(self, *, plan_id: PlanId | str, plan_version: int, work_id: WorkId | str, access: PlanAccessContext) -> MaterializationRecord | None: ...

    def mark_expired(self, *, plan_id: PlanId | str, plan_version: int, work: PlannedWork, access: PlanAccessContext, idempotency_key: str) -> PlanStoreResult: ...

    def cancel(self, *, plan_id: PlanId | str, expected_version: int, access: PlanAccessContext, idempotency_key: str, cancelled_execution_ids: tuple[str, ...]) -> PlanStoreResult: ...

    def events(self) -> tuple[EventEnvelope, ...]: ...


__all__ = [
    "DispatchPort", "DispatchReceipt", "ExecutionFactory", "MaterializationRecord", "Orchestrator",
    "PlanAccessContext", "PlanStorePort", "PlanStoreResult", "ScheduleReceipt", "SchedulingPort",
    "SupervisionPort", "SupervisionQuery",
]
