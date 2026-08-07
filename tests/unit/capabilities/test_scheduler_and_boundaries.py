from pathlib import Path

import pytest

from agentos.capabilities.models import (
    CapabilityProgram,
    CapabilityStep,
    CapabilityStepKind,
    InputBinding,
    RetryPolicy,
)
from agentos.capabilities.scheduler import DeterministicStepScheduler, ProgramValidationError


def step(name, dependencies=()):
    return CapabilityStep(
        step_id=name,
        kind=CapabilityStepKind.CHECKPOINT,
        dependencies=tuple(dependencies),
        authorization=(),
        timeout_seconds=1,
        retry_policy=RetryPolicy(),
        input_bindings=(InputBinding("ref", f"ref:{name}"),),
    )


def test_ready_steps_are_topological_deterministic_and_bounded():
    program = CapabilityProgram(steps=(step("b", ("a",)), step("a"), step("c")), compensation_steps=())
    scheduler = DeterministicStepScheduler(maximum_parallel_steps=1)
    assert scheduler.ready(program, completed=(), active=()) == ("a",)
    assert scheduler.ready(program, completed=("a",), active=()) == ("b", "c")[:1]


def test_scheduler_rejects_cycles_before_any_step_can_run():
    with pytest.raises(ProgramValidationError):
        DeterministicStepScheduler(maximum_parallel_steps=2).validate(
            CapabilityProgram(steps=(step("a", ("b",)), step("b", ("a",))), compensation_steps=())
        )


def test_capability_package_has_no_concrete_infrastructure_or_direct_tool_imports():
    root = Path("src/agentos/capabilities")
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    forbidden = (
        "FastAPI", "fastapi", "SQLAlchemy", "sqlalchemy", "Alembic", "alembic", "Redis", "redis",
        "requests", "httpx", "kafka", "rabbit", "subprocess", "playwright", "filesystem",
        "artifact_storage", "browser", "agentos.runtime", "agentos.tool_runtime",
    )
    assert not any(term in source for term in forbidden)

