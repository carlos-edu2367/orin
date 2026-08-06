from pathlib import Path

from agentos.events.models import CommitState
from agentos.orchestrator import (
    DependencyCondition,
    DependencyEdge,
    DependencyFailurePolicy,
    InMemoryDispatch,
    InMemoryPlanStore,
    InMemoryScheduling,
    InMemorySupervision,
    OrchestrationPlan,
    OrchestrationPolicy,
    OrchestratorService,
    PlannedWork,
)
from agentos.orchestrator.models import EvaluationTrigger, EvaluationTriggerKind


def test_public_exports_include_contracts_and_reference_adapters():
    assert OrchestrationPlan is not None
    assert OrchestrationPolicy(maximum_parallel_executions=1).maximum_parallel_executions == 1
    assert all(item is not None for item in (InMemoryPlanStore, InMemoryDispatch, InMemoryScheduling, InMemorySupervision, OrchestratorService))


def test_plan_events_are_minimal_and_carry_ownership_causality_fields():
    from test_models_security import draft

    store = InMemoryPlanStore()
    from agentos.orchestrator.ports import PlanAccessContext
    access = PlanAccessContext("user:1", None, "actor:1", "orchestrator.execute", "correlation:1")
    plan = OrchestrationPlan.from_draft("plan:events", draft())
    store.submit(plan, access=access, idempotency_key="submit:events", operation_fingerprint="fp")
    event = store.events()[0]
    assert event.user_id == "user:1"
    assert event.workspace_id is None
    assert event.correlation_id == "correlation:1"
    assert event.payload["plan_version"] == 1
    assert event.execution_id is None


def test_orchestrator_package_has_no_concrete_infrastructure_dependency():
    root = Path("src/agentos/orchestrator")
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    forbidden = (
        "FastAPI", "fastapi", "HTTP", "openai", "anthropic", "google", "SQLAlchemy", "sqlalchemy",
        "Alembic", "alembic", "Redis", "redis", "filesystem", "ArtifactStorage", "requests", "httpx",
        "kafka", "rabbit", "broker", "scheduler", "worker",
    )
    assert not any(term in source for term in forbidden)


def test_existing_domains_do_not_import_concrete_orchestrator_package():
    for package in ("execution", "runtime", "context", "providers", "events", "agents", "persistence"):
        source = "\n".join(path.read_text(encoding="utf-8") for path in Path(f"src/agentos/{package}").rglob("*.py"))
        assert "agentos.orchestrator" not in source

