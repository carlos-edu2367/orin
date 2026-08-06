from __future__ import annotations

from agentos.execution.models import ExecutionState
from agentos.runtime.models import (
    CompletedOutcome,
    ProviderFinal,
    ProviderToolRequest,
    ProviderUserInputRequest,
)


def test_tool_request_round_trips_through_waiting_tool(runtime_fixture):
    runtime_fixture.provider.outcomes = [
        ProviderToolRequest(action_ref="tool:search", invocation_ref="invocation:1"),
        ProviderFinal(result_ref="result:after-tool"),
    ]

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.usage.iterations == 2
    assert runtime_fixture.control.targets.count(ExecutionState.WAITING_TOOL) == 1
    assert runtime_fixture.control.targets.count(ExecutionState.RUNNING) == 2
    assert runtime_fixture.action.calls == ["tool:search"]


def test_provider_user_input_request_returns_waiting_user(runtime_fixture):
    runtime_fixture.provider.outcomes = [
        ProviderUserInputRequest(input_request_ref="input-request:1")
    ]

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert outcome.state is ExecutionState.WAITING_USER
    assert runtime_fixture.control.final_state is ExecutionState.WAITING_USER
