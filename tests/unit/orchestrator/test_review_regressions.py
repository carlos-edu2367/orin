from datetime import datetime, timedelta, timezone

import pytest

from agentos.events.models import CommitState
from agentos.execution.models import ExecutionLimits, ExecutionState, TaskSnapshot
from agentos.orchestrator import (
    EvaluationTrigger,
    EvaluationTriggerKind,
    ExecutionCreationReceipt,
    ExecutePlan,
    InMemoryDispatch,
    InMemoryPlanStore,
    InMemoryScheduling,
    InMemorySupervision,
    OrchestrationPlanDraft,
    OrchestrationPolicy,
    OrchestrationRequest,
    PlannedWork,
    RunAgentTask,
    ScheduleConstraint,
    ScheduleTrigger,
    SupervisionSnapshot,
)
from agentos.orchestrator.ports import PlanAccessContext, SupervisionQuery
from agentos.orchestrator.security import OrchestratorAccessDenied, OrchestratorValidationError, fingerprint
from agentos.orchestrator.service import OrchestratorService


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


class Resolver:
    def resolve(self, **kwargs):
        return type("Resolved", (), {"config_version": 1})()


class Factory:
    def __init__(self):
        self.count = 0

    def create(self, request):
        self.count += 1
        return ExecutionCreationReceipt(f"execution:{self.count}", 1, f"tx:{self.count}", CommitState.COMMITTED)


class UnknownFactory(Factory):
    def create(self, request):
        self.count += 1
        state = CommitState.UNKNOWN if self.count == 1 else CommitState.COMMITTED
        return ExecutionCreationReceipt(f"execution:{self.count}", 1, f"tx:{self.count}", state)


def access(actor="actor:1"):
    return PlanAccessContext("user:1", None, actor, "orchestrator.execute", "correlation:1")


def trigger(at=NOW):
    return EvaluationTrigger(EvaluationTriggerKind.MANUAL, at, actor="actor:1", user_id="user:1", purpose="orchestrator.execute", correlation_id="correlation:1")


def request(intent, key):
    return OrchestrationRequest("actor:1", "user:1", None, intent, "correlation:1", "orchestrator.execute", key, NOW)


def build(*, clock=lambda: NOW, store=None, factory=None, scheduling=None):
    store = store or InMemoryPlanStore(clock=clock)
    factory = factory or Factory()
    scheduling = scheduling or InMemoryScheduling()
    supervision = InMemorySupervision()
    service = OrchestratorService(plan_store=store, execution_factory=factory, dispatch=InMemoryDispatch(), scheduling=scheduling, resolver=Resolver(), supervision=supervision, clock=clock)
    return service, store, factory, scheduling, supervision


def test_unknown_submit_does_not_register_trigger_and_requires_inspection_before_retry():
    store = InMemoryPlanStore()
    scheduling = InMemoryScheduling()
    service, _, _, _, _ = build(store=store, scheduling=scheduling)
    node = PlannedWork("a", "agent:1", TaskSnapshot("task:a", 1), ExecutionLimits(60, 5), "work:a", "orchestrator.execute", "INTERNAL", schedule=ScheduleConstraint(NOW + timedelta(minutes=1)))
    draft = OrchestrationPlanDraft("user:1", None, "actor:1", "correlation:1", "orchestrator.execute", "INTERNAL", (node,), (), OrchestrationPolicy(), NOW)
    store.unknown_next()
    receipt = service.submit(request(ExecutePlan(draft), "submit:unknown"))
    assert receipt.commit_state is CommitState.UNKNOWN
    assert scheduling.triggers == ()
    with pytest.raises(OrchestratorValidationError):
        service.submit(request(ExecutePlan(draft), "submit:unknown"))
    inspected = store.inspect_commit(access=access(), transaction_id=receipt.transaction_id, idempotency_key="submit:unknown")
    assert inspected.commit_state is CommitState.NOT_COMMITTED


def test_scheduling_and_supervision_enforce_owner_scope():
    scheduling = InMemoryScheduling()
    trigger_record = ScheduleTrigger("trigger:1", "plan:1", "work:a", ScheduleConstraint(NOW), "schedule:1", "user:1", None, "actor:1")
    scheduling.register(trigger_record)
    with pytest.raises(OrchestratorAccessDenied):
        scheduling.cancel("trigger:1", access("actor:2"))
    supervision = InMemorySupervision()
    supervision.set(SupervisionSnapshot("execution:1", ExecutionState.RUNNING, 1, NOW), access())
    with pytest.raises(OrchestratorAccessDenied):
        supervision.observe(SupervisionQuery("execution:1", access("actor:2")))


def test_supervision_protocol_accepts_query_object():
    supervision = InMemorySupervision()
    snapshot = SupervisionSnapshot("execution:1", ExecutionState.RUNNING, 1, NOW)
    supervision.set(snapshot, access())
    assert supervision.observe(SupervisionQuery("execution:1", access())) == snapshot


def test_retry_limit_and_expiry_boundary_are_enforced():
    service, store, factory, _, supervision = build(clock=lambda: NOW + timedelta(minutes=1))
    node = PlannedWork("a", "agent:1", TaskSnapshot("task:a", 1), ExecutionLimits(60, 5), "work:a", "orchestrator.execute", "INTERNAL", schedule=ScheduleConstraint(NOW, NOW + timedelta(minutes=1)))
    draft = OrchestrationPlanDraft("user:1", None, "actor:1", "correlation:1", "orchestrator.execute", "INTERNAL", (node,), (), OrchestrationPolicy(maximum_retries=1), NOW)
    receipt = service.submit(request(ExecutePlan(draft), "submit:limit"))
    outcome = service.evaluate(receipt.plan_id, trigger())
    assert outcome.expired_work_ids == ("a",)
    assert factory.count == 0
    with pytest.raises(OrchestratorValidationError):
        fingerprint({"items": ["x" * 200 for _ in range(40)]})


def test_unknown_execution_creation_is_not_retried_without_inspection():
    factory = UnknownFactory()
    service, _, _, _, _ = build(factory=factory)
    receipt = service.submit(request(RunAgentTask("agent:1", TaskSnapshot("task:1", 1), ExecutionLimits(60, 5)), "submit:unknown-execution"))
    assert service.evaluate(receipt.plan_id, trigger()).materialized_execution_ids == ()
    assert service.evaluate(receipt.plan_id, trigger()).materialized_execution_ids == ()
    assert factory.count == 1


def test_plan_cancellation_cancels_registered_schedule_triggers():
    scheduling = InMemoryScheduling()
    service, _, _, _, _ = build(scheduling=scheduling)
    node = PlannedWork("a", "agent:1", TaskSnapshot("task:a", 1), ExecutionLimits(60, 5), "work:a", "orchestrator.execute", "INTERNAL", schedule=ScheduleConstraint(NOW + timedelta(minutes=1)))
    draft = OrchestrationPlanDraft("user:1", None, "actor:1", "correlation:1", "orchestrator.execute", "INTERNAL", (node,), (), OrchestrationPolicy(), NOW)
    receipt = service.submit(request(ExecutePlan(draft), "submit:cancel-trigger"))
    assert scheduling.triggers
    service.request_cancel(__import__("agentos.orchestrator.models", fromlist=["CancelOrchestration", "CancellationPropagationPolicy"]).CancelOrchestration("actor:1", "user:1", None, receipt.plan_id, "CANCEL_DESCENDANTS", "correlation:1", "orchestrator.execute", "cancel:trigger", NOW))
    assert scheduling.triggers == ()


def test_failure_handler_is_materialized_only_after_predecessor_failure():
    from agentos.orchestrator.models import DependencyCondition, DependencyEdge, DependencyFailurePolicy
    service, _, factory, _, supervision = build()
    nodes = (
        PlannedWork("a", "agent:1", TaskSnapshot("task:a", 1), ExecutionLimits(60, 5), "work:a", "orchestrator.execute", "INTERNAL", failure_handler_work_id="handler"),
        PlannedWork("b", "agent:1", TaskSnapshot("task:b", 1), ExecutionLimits(60, 5), "work:b", "orchestrator.execute", "INTERNAL"),
        PlannedWork("handler", "agent:1", TaskSnapshot("task:handler", 1), ExecutionLimits(60, 5), "work:handler", "orchestrator.execute", "INTERNAL"),
    )
    edges = (DependencyEdge("a", "b", DependencyCondition.COMPLETED, DependencyFailurePolicy.MATERIALIZE_FAILURE_HANDLER),)
    draft = OrchestrationPlanDraft("user:1", None, "actor:1", "correlation:1", "orchestrator.execute", "INTERNAL", nodes, edges, OrchestrationPolicy(), NOW)
    receipt = service.submit(request(ExecutePlan(draft), "submit:handler"))
    assert service.evaluate(receipt.plan_id, trigger()).materialized_execution_ids == ("execution:1",)
    supervision.set(SupervisionSnapshot("execution:1", ExecutionState.FAILED, 2, NOW), access())
    assert service.evaluate(receipt.plan_id, trigger()).materialized_execution_ids == ("execution:2",)
    assert factory.count == 2
