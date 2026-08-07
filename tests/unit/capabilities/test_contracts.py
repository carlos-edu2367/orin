from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from agentos.capabilities.models import (
    ActorRef,
    CapabilityDescriptor,
    CapabilityLimits,
    CapabilityOperationContext,
    CapabilityProgram,
    CapabilityRef,
    CapabilityRun,
    CapabilityRunState,
    CapabilityStep,
    CapabilityStepKind,
    CapabilityStatus,
    CompensationPolicy,
    InputBinding,
    PermissionRequirement,
    RetryPolicy,
    StructuredValue,
    ToolRef,
    execution_state_for_capability_state,
)


NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)


def context(**changes):
    value = CapabilityOperationContext(
        user_id="user:1",
        workspace_id="workspace:1",
        agent_id="agent:1",
        execution_id="execution:1",
        correlation_id="correlation:1",
        purpose="capability.test",
        actor=ActorRef("actor:1"),
    )
    return replace(value, **changes)


def descriptor(program=None):
    return CapabilityDescriptor(
        capability_ref=CapabilityRef("capability:test", 1),
        name="test-capability",
        description="bounded capability",
        input_schema="schema:input",
        output_schema="schema:output",
        allowed_tools=(ToolRef("tool:test", 1),),
        allowed_child_capabilities=(),
        permissions=(PermissionRequirement("resource:test", "read"),),
        limits=CapabilityLimits(
            timeout_seconds=60,
            maximum_steps=4,
            maximum_tool_invocations=2,
            maximum_child_executions=1,
            maximum_parallel_steps=2,
            maximum_cost=Decimal("10"),
            maximum_resource_usage=100,
        ),
        cancellation_policy="COOPERATIVE",
        compensation_policy=CompensationPolicy.NONE,
        status=CapabilityStatus.ACTIVE,
    )


def test_context_is_complete_and_repr_hides_purpose():
    value = context()
    assert value.execution_id == "execution:1"
    assert "capability.test" not in repr(value)
    with pytest.raises(ValueError):
        context(purpose="")


def test_models_are_immutable_and_limits_are_effective_bounds():
    limits = descriptor().limits
    with pytest.raises(FrozenInstanceError):
        limits.maximum_steps = 99
    with pytest.raises(ValueError):
        CapabilityLimits(0, 1, 1, 0, 1, None, 1)


def test_structured_values_are_bounded_and_reject_secret_fields():
    value = StructuredValue.from_mapping({"input_ref": "ref:1", "count": 2})
    assert value.items[0][0] == "count"
    with pytest.raises(ValueError):
        StructuredValue.from_mapping({"secret": "not allowed"})


def test_program_rejects_duplicate_steps_and_unknown_dependencies():
    step = CapabilityStep(
        step_id="step:a",
        kind=CapabilityStepKind.CHECKPOINT,
        dependencies=(),
        authorization=(),
        timeout_seconds=1,
        retry_policy=RetryPolicy(),
        input_bindings=(InputBinding("input", "ref:1"),),
    )
    with pytest.raises(ValueError):
        CapabilityProgram(steps=(step, replace(step)), compensation_steps=())
    with pytest.raises(ValueError):
        CapabilityProgram(steps=(replace(step, dependencies=("missing",)),), compensation_steps=())


@pytest.mark.parametrize(
    ("run_state", "execution_state"),
    [
        (CapabilityRunState.QUEUED, "QUEUED"),
        (CapabilityRunState.RUNNING, "RUNNING"),
        (CapabilityRunState.COMPENSATING, "RUNNING"),
        (CapabilityRunState.WAITING_TOOL, "WAITING_TOOL"),
        (CapabilityRunState.WAITING_CHILD, "PAUSED"),
        (CapabilityRunState.PAUSED, "PAUSED"),
        (CapabilityRunState.SUCCEEDED, "COMPLETED"),
        (CapabilityRunState.FAILED, "FAILED"),
        (CapabilityRunState.CANCELLED, "CANCELLED"),
    ],
)
def test_capability_state_maps_to_canonical_execution(run_state, execution_state):
    assert execution_state_for_capability_state(run_state).value == execution_state

