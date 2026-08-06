from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agentos.execution.models import ExecutionLimits, TaskSnapshot
from agentos.orchestrator.models import (
    DependencyCondition,
    DependencyEdge,
    DependencyFailurePolicy,
    OpaqueReference,
    OrchestrationPlanDraft,
    OrchestrationPlan,
    OrchestrationPolicy,
    PlannedWork,
    ScheduleConstraint,
)
from agentos.orchestrator.security import (
    OrchestratorValidationError,
    fingerprint,
    validate_plan,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def limits() -> ExecutionLimits:
    return ExecutionLimits(max_duration_seconds=60, max_iterations=5, max_cost=Decimal("1"))


def work(work_id: str, *, schedule=None) -> PlannedWork:
    return PlannedWork(
        work_id=work_id,
        agent_id="agent:1",
        task=TaskSnapshot(f"task:{work_id}", 1),
        limits=limits(),
        idempotency_key=f"work:{work_id}:1",
        purpose="orchestrator.execute",
        classification="INTERNAL",
        schedule=schedule,
    )


def draft(*, nodes=None, edges=()) -> OrchestrationPlanDraft:
    return OrchestrationPlanDraft(
        user_id="user:1",
        workspace_id=None,
        actor="actor:1",
        correlation_id="correlation:1",
        purpose="orchestrator.execute",
        classification="INTERNAL",
        nodes=tuple(nodes or (work("a"),)),
        dependencies=tuple(edges),
        policy=OrchestrationPolicy(maximum_parallel_executions=2),
        created_at=NOW,
    )


def test_rejects_unbounded_or_sensitive_opaque_references():
    with pytest.raises(ValueError):
        OpaqueReference("prompt:full-secret")
    with pytest.raises(ValueError):
        OpaqueReference("x" * 256)


def test_rejects_cycles_duplicate_edges_and_unknown_nodes_before_persistence():
    nodes = (work("a"), work("b"))
    edge = DependencyEdge("a", "b", DependencyCondition.COMPLETED, DependencyFailurePolicy.DO_NOT_MATERIALIZE)
    with pytest.raises(OrchestratorValidationError):
        validate_plan(draft(nodes=nodes, edges=(edge, edge)))
    with pytest.raises(OrchestratorValidationError):
        validate_plan(draft(nodes=nodes, edges=(DependencyEdge("a", "a", DependencyCondition.COMPLETED, DependencyFailurePolicy.DO_NOT_MATERIALIZE),)))
    with pytest.raises(OrchestratorValidationError):
        validate_plan(draft(nodes=nodes, edges=(edge, DependencyEdge("b", "a", DependencyCondition.COMPLETED, DependencyFailurePolicy.DO_NOT_MATERIALIZE))))
    with pytest.raises(OrchestratorValidationError):
        validate_plan(draft(nodes=nodes, edges=(DependencyEdge("a", "missing", DependencyCondition.COMPLETED, DependencyFailurePolicy.DO_NOT_MATERIALIZE),)))


def test_schedule_requires_bounded_ordered_aware_timestamps():
    with pytest.raises(ValueError):
        ScheduleConstraint(not_before=NOW + timedelta(seconds=1), expires_at=NOW)
    with pytest.raises(ValueError):
        ScheduleConstraint(not_before=datetime(2026, 8, 6, 12, 0))


def test_fingerprint_is_stable_bounded_and_changes_with_semantics():
    first = fingerprint(draft())
    second = fingerprint(draft())
    changed = fingerprint(draft(nodes=(work("different"),)))
    assert first == second
    assert first != changed
    assert len(first) == 64


def test_plan_from_draft_creates_a_versioned_immutable_plan():
    plan = OrchestrationPlan.from_draft("plan:1", draft())
    assert plan.plan_id == "plan:1"
    assert plan.version == 1
    assert plan.nodes[0].work_id == "a"
