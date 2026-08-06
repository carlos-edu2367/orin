from datetime import datetime, timedelta, timezone

import pytest

from agentos.events.models import CommitState
from agentos.execution.models import ExecutionLimits, ExecutionState, TaskSnapshot
from agentos.orchestrator.in_memory import InMemoryDispatch, InMemoryPlanStore, InMemoryScheduling, InMemorySupervision
from agentos.orchestrator.models import (
    DependencyCondition,
    DependencyEdge,
    DependencyFailurePolicy,
    EvaluationTrigger,
    EvaluationTriggerKind,
    ExecutionCreationReceipt,
    ExecutePlan,
    OrchestrationRequest,
    OrchestrationPlanDraft,
    OrchestrationPolicy,
    PlannedWork,
    RunAgentTask,
    ScheduleConstraint,
)
from agentos.orchestrator.service import OrchestratorService
from agentos.orchestrator.security import OrchestratorIdempotencyConflict


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


class Resolver:
    def resolve(self, **kwargs):
        return type("Resolved", (), {"config_version": 7, "agent_id": kwargs["agent_id"]})()


class Factory:
    def __init__(self):
        self.requests = []
        self.counter = 0

    def create(self, request):
        self.requests.append(request)
        self.counter += 1
        return ExecutionCreationReceipt(f"execution:{self.counter}", 1, f"tx:{self.counter}", CommitState.COMMITTED)


def access_fields():
    return {"actor": "actor:1", "user_id": "user:1", "workspace_id": None, "purpose": "orchestrator.execute", "correlation_id": "correlation:1"}


def trigger():
    return EvaluationTrigger(EvaluationTriggerKind.MANUAL, NOW, actor="actor:1", user_id="user:1", workspace_id=None, purpose="orchestrator.execute", correlation_id="correlation:1")


def task_request(intent, key="submit:1"):
    return OrchestrationRequest(**access_fields(), intent=intent, idempotency_key=key, requested_at=NOW)


def service(*, clock=lambda: NOW, resolver=None, supervision=None):
    factory = Factory()
    store = InMemoryPlanStore(clock=clock)
    return (
        OrchestratorService(
            plan_store=store,
            execution_factory=factory,
            dispatch=InMemoryDispatch(),
            scheduling=InMemoryScheduling(),
            resolver=resolver or Resolver(),
            supervision=supervision or InMemorySupervision(),
            clock=clock,
        ),
        store,
        factory,
    )


def work(work_id, *, schedule=None):
    return PlannedWork(work_id, "agent:1", TaskSnapshot(f"task:{work_id}", 1), ExecutionLimits(60, 5), f"work:{work_id}", "orchestrator.execute", "INTERNAL", schedule=schedule)


def test_submit_is_idempotent_and_evaluate_materializes_only_after_agent_revalidation():
    orchestrator, store, factory = service()
    request = task_request(RunAgentTask("agent:1", TaskSnapshot("task:1", 1), ExecutionLimits(60, 5)))
    first = orchestrator.submit(request)
    repeated = orchestrator.submit(request)
    assert first == repeated
    outcome = orchestrator.evaluate(first.plan_id, trigger())
    assert outcome.materialized_execution_ids == ("execution:1",)
    assert factory.requests[0].agent_config_version == 7
    assert store.events()[2].event_type == "WorkMaterialized"


def test_divergent_submit_fingerprint_is_a_sanitized_conflict():
    orchestrator, _, _ = service()
    orchestrator.submit(task_request(RunAgentTask("agent:1", TaskSnapshot("task:1", 1), ExecutionLimits(60, 5))))
    with pytest.raises(OrchestratorIdempotencyConflict) as error:
        orchestrator.submit(task_request(RunAgentTask("agent:1", TaskSnapshot("task:2", 1), ExecutionLimits(60, 5))))
    assert "task" not in str(error.value).lower()


def test_schedule_window_prevents_early_and_late_materialization():
    orchestrator, _, factory = service()
    early = OrchestrationPlanDraft(**{**access_fields(), "classification": "INTERNAL", "nodes": (work("a", schedule=ScheduleConstraint(NOW + timedelta(minutes=5), NOW + timedelta(minutes=10))),), "dependencies": (), "policy": OrchestrationPolicy(maximum_parallel_executions=1), "created_at": NOW})
    receipt = orchestrator.submit(task_request(ExecutePlan(early), "submit:early"))
    outcome = orchestrator.evaluate(receipt.plan_id, trigger())
    assert outcome.materialized_execution_ids == ()
    late_orchestrator, _, late_factory = service(clock=lambda: NOW + timedelta(minutes=11))
    late = OrchestrationPlanDraft(**{**access_fields(), "classification": "INTERNAL", "nodes": (work("a", schedule=ScheduleConstraint(NOW, NOW + timedelta(minutes=1))),), "dependencies": (), "policy": OrchestrationPolicy(maximum_parallel_executions=1), "created_at": NOW})
    late_receipt = late_orchestrator.submit(task_request(ExecutePlan(late), "submit:late"))
    late_outcome = late_orchestrator.evaluate(late_receipt.plan_id, trigger())
    assert late_outcome.expired_work_ids == ("a",)
    assert late_factory.requests == []
    assert factory.requests == []


def test_dependency_requires_completed_predecessor_before_successor():
    supervision = InMemorySupervision()
    orchestrator, store, factory = service(supervision=supervision)
    first = work("a")
    second = work("b")
    plan = OrchestrationPlanDraft(**{**access_fields(), "classification": "INTERNAL", "nodes": (first, second), "dependencies": (DependencyEdge("a", "b", DependencyCondition.COMPLETED, DependencyFailurePolicy.DO_NOT_MATERIALIZE),), "policy": OrchestrationPolicy(maximum_parallel_executions=2), "created_at": NOW})
    receipt = orchestrator.submit(task_request(ExecutePlan(plan), "submit:deps"))
    initial = orchestrator.evaluate(receipt.plan_id, trigger())
    assert initial.materialized_execution_ids == ("execution:1",)
    supervision.set(type("Snapshot", (), {"execution_id": "execution:1", "observed_state": ExecutionState.COMPLETED, "state_version": 2, "last_progress_at": NOW, "pending_action_ref": None})())
    next_outcome = orchestrator.evaluate(receipt.plan_id, trigger())
    assert next_outcome.materialized_execution_ids == ("execution:2",)
    assert len(factory.requests) == 2

