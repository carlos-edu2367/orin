from datetime import datetime, timedelta, timezone

from agentos.execution.models import ExecutionLimits, ExecutionState, TaskSnapshot
from agentos.execution.ports import Accepted
from agentos.orchestrator.compat import ExecutionCancellationAdapter
from agentos.orchestrator.in_memory import InMemoryDispatch, InMemoryPlanStore, InMemoryScheduling, InMemorySupervision
from agentos.orchestrator.models import (
    CancelOrchestration,
    CancellationPropagationPolicy,
    DependencyCondition,
    DependencyEdge,
    DependencyFailurePolicy,
    EvaluationTrigger,
    EvaluationTriggerKind,
    ExecutionCreationReceipt,
    ExecutePlan,
    OrchestrationPlanDraft,
    OrchestrationPolicy,
    OrchestrationRequest,
    RetryExecution,
    RunAgentTask,
    ScheduleConstraint,
)
from agentos.orchestrator.service import OrchestratorService
from agentos.events.models import CommitState


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


class Control:
    def __init__(self):
        self.commands = []

    def request_cancel(self, command):
        self.commands.append(command)
        return Accepted(command.expected_version + 1, "tx:cancel")


class Resolver:
    def resolve(self, **kwargs):
        return type("Resolved", (), {"config_version": 2})()


class Factory:
    def __init__(self):
        self.count = 0

    def create(self, request):
        self.count += 1
        return ExecutionCreationReceipt(f"execution:{self.count}", 1, f"tx:{self.count}", CommitState.COMMITTED)


def access():
    return {"actor": "actor:1", "user_id": "user:1", "workspace_id": None, "purpose": "orchestrator.execute", "correlation_id": "correlation:1"}


def trigger():
    return EvaluationTrigger(EvaluationTriggerKind.MANUAL, NOW, actor="actor:1", user_id="user:1", purpose="orchestrator.execute", correlation_id="correlation:1")


def build():
    factory = Factory()
    supervision = InMemorySupervision()
    control = Control()
    orchestrator = OrchestratorService(
        plan_store=InMemoryPlanStore(clock=lambda: NOW), execution_factory=factory, dispatch=InMemoryDispatch(),
        scheduling=InMemoryScheduling(), resolver=Resolver(), supervision=supervision,
        cancellation=ExecutionCancellationAdapter(control), clock=lambda: NOW,
    )
    return orchestrator, supervision, factory, control


def request(intent, key):
    return OrchestrationRequest(**access(), intent=intent, idempotency_key=key, requested_at=NOW)


def test_cancel_before_materialization_prevents_future_attempts():
    orchestrator, _, factory, _ = build()
    schedule = ScheduleConstraint(NOW + timedelta(minutes=5), NOW + timedelta(minutes=10))
    node = __import__("agentos.orchestrator.models", fromlist=["PlannedWork"]).PlannedWork("a", "agent:1", TaskSnapshot("task:a", 1), ExecutionLimits(60, 5), "work:a", "orchestrator.execute", "INTERNAL", schedule=schedule)
    draft = OrchestrationPlanDraft(**access(), classification="INTERNAL", nodes=(node,), dependencies=(), policy=OrchestrationPolicy(), created_at=NOW)
    receipt = orchestrator.submit(request(ExecutePlan(draft), "submit:cancel-before"))
    cancelled = orchestrator.request_cancel(CancelOrchestration(**access(), plan_id=receipt.plan_id, policy=CancellationPropagationPolicy.CANCEL_DESCENDANTS, idempotency_key="cancel:1", requested_at=NOW))
    assert cancelled.cancelled_execution_ids == ()
    assert factory.count == 0
    assert orchestrator.evaluate(receipt.plan_id, trigger()).materialized_execution_ids == ()


def test_cancel_after_materialization_uses_kernel_version_and_does_not_reopen_terminal():
    orchestrator, supervision, factory, control = build()
    receipt = orchestrator.submit(request(RunAgentTask("agent:1", TaskSnapshot("task:1", 1), ExecutionLimits(60, 5)), "submit:cancel-after"))
    orchestrator.evaluate(receipt.plan_id, trigger())
    supervision.set(__import__("agentos.orchestrator.models", fromlist=["SupervisionSnapshot"]).SupervisionSnapshot("execution:1", ExecutionState.RUNNING, 3, NOW))
    cancelled = orchestrator.request_cancel(CancelOrchestration(**access(), plan_id=receipt.plan_id, policy=CancellationPropagationPolicy.CANCEL_DESCENDANTS, idempotency_key="cancel:2", requested_at=NOW))
    assert cancelled.cancelled_execution_ids == ("execution:1",)
    assert control.commands[0].expected_version == 3


def test_retry_of_terminal_attempt_creates_new_execution_and_preserves_previous():
    orchestrator, supervision, factory, _ = build()
    receipt = orchestrator.submit(request(RunAgentTask("agent:1", TaskSnapshot("task:1", 1), ExecutionLimits(60, 5)), "submit:retry"))
    orchestrator.evaluate(receipt.plan_id, trigger())
    supervision.set(__import__("agentos.orchestrator.models", fromlist=["SupervisionSnapshot"]).SupervisionSnapshot("execution:1", ExecutionState.FAILED, 4, NOW))
    plan = orchestrator._store.get(receipt.plan_id, __import__("agentos.orchestrator.ports", fromlist=["PlanAccessContext"]).PlanAccessContext("user:1", None, "actor:1", "orchestrator.execute", "correlation:1"))
    retry = orchestrator.request_retry(RetryExecution(**access(), plan_id=receipt.plan_id, work_id=plan.nodes[0].work_id, previous_execution_id="execution:1", idempotency_key="retry:1", requested_at=NOW, expected_plan_version=1))
    assert retry.execution_id == "execution:2"
    assert retry.previous_execution_id == "execution:1"
    assert factory.count == 2


def test_failed_predecessor_with_do_not_materialize_keeps_successor_planned():
    orchestrator, supervision, factory, _ = build()
    from agentos.orchestrator.models import DependencyCondition, DependencyEdge, DependencyFailurePolicy, PlannedWork
    nodes = (
        PlannedWork("a", "agent:1", TaskSnapshot("task:a", 1), ExecutionLimits(60, 5), "work:a", "orchestrator.execute", "INTERNAL"),
        PlannedWork("b", "agent:1", TaskSnapshot("task:b", 1), ExecutionLimits(60, 5), "work:b", "orchestrator.execute", "INTERNAL"),
    )
    draft = OrchestrationPlanDraft(**access(), classification="INTERNAL", nodes=nodes, dependencies=(DependencyEdge("a", "b", DependencyCondition.COMPLETED, DependencyFailurePolicy.DO_NOT_MATERIALIZE),), policy=OrchestrationPolicy(), created_at=NOW)
    receipt = orchestrator.submit(request(ExecutePlan(draft), "submit:failure"))
    orchestrator.evaluate(receipt.plan_id, trigger())
    supervision.set(__import__("agentos.orchestrator.models", fromlist=["SupervisionSnapshot"]).SupervisionSnapshot("execution:1", ExecutionState.FAILED, 2, NOW))
    assert orchestrator.evaluate(receipt.plan_id, trigger()).materialized_execution_ids == ()
    assert factory.count == 1
