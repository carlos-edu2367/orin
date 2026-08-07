from dataclasses import replace

from agentos.capabilities.models import CapabilityFailed, Retryability
from agentos.capabilities.ports import EffectState, ToolFailed, ToolSucceeded
from agentos.capabilities.service import CapabilityService
from .test_service_lifecycle import make_service, ctx
from agentos.capabilities.models import RunCapability


class DenyAll:
    def authorize(self, context, descriptor, step, arguments):
        return False


class SequenceTool:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def invoke(self, request):
        self.calls.append(request)
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        return replace(outcome, invocation_id=request.invocation_id)

    def request_cancel(self, request):
        return None


def activate(service, control, accepted, expected_run_version=1):
    from agentos.execution.ports import AcquireExecution, ExecutionCommandContext
    from datetime import datetime, timezone

    now = datetime(2026, 8, 7, tzinfo=timezone.utc)
    command_context = ExecutionCommandContext(
        user_id="user:1", workspace_id="workspace:1", agent_id="agent:1", execution_id=accepted.execution_id,
        correlation_id="correlation:1", purpose="capability.test",
    )
    control.acquire(AcquireExecution(command_context, "acquire:1", "acquire:1", 1, now, "worker:1"))
    return service.run(RunCapability(accepted.capability_run_id, ctx(accepted.execution_id), expected_run_version))


def test_step_authorization_is_intersection_and_denies_before_tool_effect():
    service, control, _persistence, _state, tool, _child, request = make_service(authorization=DenyAll())
    accepted = service.start(request)
    outcome = activate(service, control, accepted)
    assert isinstance(outcome, CapabilityFailed)
    assert outcome.error_code == "step_not_authorized"
    assert tool.calls == []


def test_unknown_effect_blocks_retry_even_when_tool_reports_safe_retryability():
    unknown = ToolFailed("invocation", "timeout", Retryability.SAFE, EffectState.UNKNOWN)
    service, control, _persistence, _state, tool, _child, request = make_service(tool_result=unknown)
    accepted = service.start(request)
    outcome = activate(service, control, accepted)
    assert isinstance(outcome, CapabilityFailed)
    assert len(tool.calls) == 1


def test_failed_tool_attempt_is_counted_in_usage_without_becoming_success():
    unknown = ToolFailed("invocation", "failed", Retryability.NEVER, EffectState.NOT_APPLIED)
    service, control, _persistence, state, tool, _child, request = make_service(tool_result=unknown)
    accepted = service.start(request)
    outcome = activate(service, control, accepted)
    assert isinstance(outcome, CapabilityFailed)
    run = state.load(accepted.capability_run_id, ctx(accepted.execution_id))
    assert run.usage.tool_invocations == 1


def test_safe_retry_uses_a_new_deterministic_attempt_key():
    tool_result = ToolFailed("invocation", "temporary", Retryability.SAFE, EffectState.NOT_APPLIED)
    service, control, _persistence, _state, original_tool, _child, request = make_service(tool_result=tool_result, maximum_attempts=2)
    sequence = SequenceTool([tool_result, ToolSucceeded("invocation", "result:retry")])
    service._tool_port = sequence
    accepted = service.start(request)
    outcome = activate(service, control, accepted)
    assert outcome.result_ref == "result:retry"
    assert len(sequence.calls) == 2
    assert sequence.calls[0].idempotency_key != sequence.calls[1].idempotency_key


def test_disabled_descriptor_is_revalidated_before_resume_or_effect():
    from agentos.capabilities.models import CapabilityRegistryOperationContext, DisableCapability, CapabilityRef, CapabilityStatus

    service, control, _persistence, _state, tool, _child, request = make_service()
    accepted = service.start(request)
    service._registry.disable(DisableCapability(
        request_id="disable:1",
        context=CapabilityRegistryOperationContext(
            user_id="user:1", workspace_id="workspace:1", agent_id=None, execution_id=None,
            administrative_correlation_id="admin:1", correlation_id="correlation:1",
            purpose="catalog.maintain", actor="actor:1",
        ),
        capability_ref=CapabilityRef("capability:test", 1), expected_status=CapabilityStatus.ACTIVE,
        reason="disabled for policy", idempotency_key="disable:1",
    ))
    outcome = activate(service, control, accepted)
    assert isinstance(outcome, CapabilityFailed)
    assert outcome.error_code == "capability_disabled"
    assert tool.calls == []
