from datetime import datetime, timezone

import pytest

from agentos.events.models import CommitState
from agentos.orchestrator.in_memory import InMemoryDispatch, InMemoryPlanStore, InMemoryScheduling, InMemorySupervision
from agentos.orchestrator.models import (
    DispatchRequest,
    EvaluationTrigger,
    EvaluationTriggerKind,
    OrchestrationPlan,
    ProcessingClass,
    ScheduleConstraint,
    ScheduleTrigger,
    SupervisionSnapshot,
)
from agentos.orchestrator.ports import PlanAccessContext
from agentos.orchestrator.security import OrchestratorAccessDenied, OrchestratorIdempotencyConflict

from test_models_security import NOW, draft


def access(actor: str = "actor:1") -> PlanAccessContext:
    return PlanAccessContext("user:1", None, actor, "orchestrator.execute", "correlation:1")


def test_plan_store_is_idempotent_and_hides_cross_owner_plans():
    store = InMemoryPlanStore()
    plan = OrchestrationPlan.from_draft("plan:1", draft())
    first = store.submit(plan, access=access(), idempotency_key="submit:1", operation_fingerprint="fp:1")
    repeated = store.submit(plan, access=access(), idempotency_key="submit:1", operation_fingerprint="fp:1")
    assert first.receipt == repeated.receipt
    with pytest.raises(OrchestratorIdempotencyConflict):
        store.submit(plan, access=access(), idempotency_key="submit:1", operation_fingerprint="different")
    with pytest.raises(OrchestratorAccessDenied):
        store.get("plan:1", PlanAccessContext("user:2", None, "actor:2", "orchestrator.execute", "correlation:2"))


def test_plan_store_requires_inspection_for_unknown_and_supports_safe_commit_states():
    store = InMemoryPlanStore()
    store.unknown_next()
    plan = OrchestrationPlan.from_draft("plan:unknown", draft())
    result = store.submit(plan, access=access(), idempotency_key="submit:unknown", operation_fingerprint="fp")
    assert result.receipt.commit_state is CommitState.UNKNOWN
    inspected = store.inspect_commit(access=access(), transaction_id=result.receipt.transaction_id, idempotency_key="submit:unknown")
    assert inspected.commit_state is CommitState.NOT_COMMITTED


def test_dispatch_is_minimal_and_idempotent():
    dispatch = InMemoryDispatch()
    request = DispatchRequest("execution:1", 1, ProcessingClass.STANDARD, "correlation:1", "orchestrator.execute", "dispatch:1")
    first = dispatch.request_dispatch(request)
    second = dispatch.request_dispatch(request)
    assert first.accepted is True
    assert second.already_applied is True
    assert dispatch.dispatches == (request,)


def test_scheduling_only_records_and_cancels_triggers():
    scheduling = InMemoryScheduling()
    trigger = ScheduleTrigger("trigger:1", "plan:1", "work:a", ScheduleConstraint(NOW), "schedule:1")
    assert scheduling.register(trigger).trigger_id == "trigger:1"
    assert scheduling.cancel("trigger:1", access()) is True
    assert scheduling.triggers == ()


def test_supervision_observes_without_mutating_snapshot():
    supervision = InMemorySupervision()
    snapshot = SupervisionSnapshot("execution:1", "RUNNING", 2, NOW)
    supervision.set(snapshot)
    assert supervision.observe("execution:1", access()).state_version == 2
    assert supervision.observed == (snapshot,)
