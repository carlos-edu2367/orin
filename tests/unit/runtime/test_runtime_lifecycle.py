from __future__ import annotations

from agentos.execution.models import ExecutionState
from agentos.execution.ports import ControlSignal
from agentos.runtime.models import CancelledOutcome, CompletedOutcome


def test_execute_acquires_starts_and_completes_simple_execution(runtime_fixture):
    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, CompletedOutcome)
    assert runtime_fixture.control.targets == [
        ExecutionState.STARTING,
        ExecutionState.RUNNING,
        ExecutionState.COMPLETED,
    ]
    assert runtime_fixture.context.finalized == [("execution-1", ExecutionState.COMPLETED)]


def test_cancelled_signal_is_terminal_and_provider_is_not_called(runtime_fixture):
    runtime_fixture.control.signal = ControlSignal.CANCEL_REQUESTED

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, CancelledOutcome)
    assert runtime_fixture.provider.calls == []
