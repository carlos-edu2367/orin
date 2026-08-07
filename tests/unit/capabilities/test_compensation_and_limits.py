from dataclasses import replace
from agentos.capabilities.models import CapabilityFailed, CompensationPolicy
from agentos.capabilities.ports import EffectState, ToolFailed, ToolSucceeded
from agentos.execution.models import ExecutionState
from .test_security_limits_retry_compensation import SequenceTool, activate
from .test_service_lifecycle import make_service


def test_declared_compensation_runs_in_order_and_remains_visible_on_failure():
    tool = SequenceTool([
        ToolSucceeded("invocation", "result:1"),
        ToolFailed("invocation", "main-failed", effect_state=EffectState.NOT_APPLIED),
        ToolSucceeded("invocation", "compensated"),
    ])
    service, control, persistence, state, _original, _child, request = make_service(
        include_second_step=True, compensation_policy=CompensationPolicy.EXPLICIT_STEPS
    )
    service._tool_port = tool
    accepted = service.start(request)
    outcome = activate(service, control, accepted)
    assert isinstance(outcome, CapabilityFailed)
    assert outcome.compensation.complete is True
    assert len(tool.calls) == 3
    assert any(event.event_type.value == "CapabilityCompensationFinished" for event in state.events())
    assert persistence.get(accepted.execution_id).state is ExecutionState.FAILED


def test_compensation_failure_never_becomes_success():
    tool = SequenceTool([
        ToolSucceeded("invocation", "result:1"),
        ToolFailed("invocation", "main-failed", effect_state=EffectState.NOT_APPLIED),
        ToolFailed("invocation", "comp-failed", effect_state=EffectState.NOT_APPLIED),
    ])
    service, control, _persistence, _state, _original, _child, request = make_service(
        include_second_step=True, compensation_policy=CompensationPolicy.EXPLICIT_STEPS
    )
    service._tool_port = tool
    accepted = service.start(request)
    outcome = activate(service, control, accepted)
    assert isinstance(outcome, CapabilityFailed)
    assert outcome.compensation.complete is False


def test_tool_invocation_limit_stops_next_step_after_confirmed_usage():
    tool = SequenceTool([ToolSucceeded("invocation", "result:1"), ToolSucceeded("invocation", "result:2")])
    service, control, _persistence, _state, _original, _child, request = make_service(
        include_second_step=True, maximum_tool_invocations=1
    )
    service._tool_port = tool
    accepted = service.start(request)
    outcome = activate(service, control, accepted)
    assert isinstance(outcome, CapabilityFailed)
    assert outcome.error_code == "maximum_tool_invocations"
    assert len(tool.calls) == 1


def test_timeout_is_enforced_before_starting_a_new_effect():
    from datetime import timedelta

    service, control, _persistence, state, tool, _child, request = make_service()
    accepted = service.start(request)
    from datetime import datetime, timezone
    run = state.load(accepted.capability_run_id, __import__("tests.unit.capabilities.test_service_lifecycle", fromlist=["ctx"]).ctx(accepted.execution_id))
    state.save(replace(run, started_at=datetime(2026, 8, 7, tzinfo=timezone.utc)), expected_version=run.state_version)
    service._clock = lambda: datetime(2026, 8, 7, tzinfo=timezone.utc) + timedelta(seconds=61)
    outcome = activate(service, control, accepted, expected_run_version=2)
    assert isinstance(outcome, CapabilityFailed)
    assert outcome.error_code == "timeout"
    assert tool.calls == []
