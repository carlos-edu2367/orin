from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from threading import Event, Thread

from agentos.execution.models import CancellationReason, CancellationReasonCode, ExecutionState
from agentos.execution.ports import ControlSignal, Indeterminate, Rejected
from agentos.runtime.models import (
    ActionFailed,
    ActionSucceeded,
    CompletedOutcome,
    ProviderFinal,
    ProviderToolRequest,
    ProviderCancelled,
    Retryability,
    RuntimeErrorCategory,
    RuntimeErrorInfo,
    RuntimeLimits,
    RuntimeUsage,
    CancelledOutcome,
    FailedOutcome,
    WaitingOutcome,
)


def test_cancel_after_provider_result_never_completes(runtime_fixture):
    def cancel_after_provider(_request):
        runtime_fixture.control.signal = ControlSignal.CANCEL_REQUESTED

    runtime_fixture.provider.on_call = cancel_after_provider

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, CancelledOutcome)
    assert outcome.reason.code is CancellationReasonCode.USER_REQUESTED
    assert runtime_fixture.control.final_state is ExecutionState.CANCELLED
    assert ExecutionState.COMPLETED not in runtime_fixture.control.targets


def test_cancel_rejection_is_not_reported_as_confirmed_cancellation(runtime_fixture):
    runtime_fixture.control.signal = ControlSignal.CANCEL_REQUESTED
    runtime_fixture.control.request_cancel = lambda _command: Rejected(
        reason="INVALID_TRANSITION", current_state=runtime_fixture.control.execution.state
    )

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.RECONCILIATION
    assert runtime_fixture.control.final_state is ExecutionState.QUEUED


def test_unknown_cancel_commit_is_not_reported_as_confirmed_cancellation(runtime_fixture):
    runtime_fixture.provider.outcomes = [
        ProviderCancelled(CancellationReason(CancellationReasonCode.USER_REQUESTED))
    ]
    runtime_fixture.control.commit = lambda _command: Indeterminate("transaction:unknown")

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.RECONCILIATION
    assert runtime_fixture.control.final_state is ExecutionState.RUNNING


def test_provider_cancellation_persists_consumed_usage(runtime_fixture):
    runtime_fixture.provider.outcomes = [
        ProviderCancelled(
            CancellationReason(CancellationReasonCode.USER_REQUESTED),
            RuntimeUsage(iterations=1, provider_tokens=5, cost=2),
        )
    ]

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, CancelledOutcome)
    assert runtime_fixture.control.execution.usage.provider_tokens == 5
    assert runtime_fixture.control.execution.usage.cost == 2


def test_provider_indeterminate_is_not_reported_as_provider_success(runtime_fixture):
    from agentos.runtime.models import ProviderIndeterminate

    runtime_fixture.provider.outcomes = [
        ProviderIndeterminate(
            RuntimeErrorInfo(RuntimeErrorCategory.RECONCILIATION, "PROVIDER_INDETERMINATE", Retryability.POLICY_DEPENDENT),
            RuntimeUsage(iterations=1, provider_tokens=3),
        )
    ]

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.RECONCILIATION
    assert runtime_fixture.control.execution.usage.provider_tokens == 3


def test_pause_after_provider_result_returns_paused(runtime_fixture):
    def pause_after_provider(_request):
        runtime_fixture.control.signal = ControlSignal.PAUSE_REQUESTED

    runtime_fixture.provider.on_call = pause_after_provider

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, WaitingOutcome)
    assert outcome.state is ExecutionState.PAUSED
    assert runtime_fixture.control.final_state is ExecutionState.PAUSED
    assert any(change.kind == "checkpoint-recorded" for change in runtime_fixture.control.changes)


def test_cost_limit_is_failed_after_public_provider_measurement(runtime_fixture):
    runtime_fixture.runtime._limits = RuntimeLimits(max_cost=Decimal("1"))
    runtime_fixture.provider.outcomes = [
        ProviderFinal("result:too-expensive", RuntimeUsage(iterations=1, cost=Decimal("2")))
    ]

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.LIMIT
    assert runtime_fixture.control.final_state is ExecutionState.FAILED


def test_token_limit_is_failed_after_public_provider_measurement(runtime_fixture):
    runtime_fixture.runtime._limits = RuntimeLimits(max_provider_tokens=1)
    runtime_fixture.provider.outcomes = [
        ProviderFinal("result:too-many-tokens", RuntimeUsage(iterations=1, provider_tokens=2))
    ]

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.LIMIT
    assert runtime_fixture.control.final_state is ExecutionState.FAILED


def test_execution_timeout_is_distinct_from_provider_timeout(runtime_fixture):
    runtime_fixture.runtime._limits = RuntimeLimits(max_duration_seconds=1)
    runtime_fixture.clock.monotonic_values = [0.0, 2.0]

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.EXECUTION_TIMEOUT
    assert runtime_fixture.control.final_state is ExecutionState.FAILED


def test_execution_deadline_is_checked_after_provider_effect(runtime_fixture):
    runtime_fixture.runtime._limits = RuntimeLimits(max_duration_seconds=1)
    runtime_fixture.clock.monotonic_values = [0.0, 0.0, 0.0, 2.0]
    runtime_fixture.provider.outcomes = [ProviderFinal("result:late")]

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.EXECUTION_TIMEOUT
    assert runtime_fixture.control.final_state is ExecutionState.FAILED


def test_provider_timeout_is_distinct_when_effect_exceeds_provider_budget(runtime_fixture):
    runtime_fixture.runtime._limits = RuntimeLimits(
        max_duration_seconds=60,
        provider_timeout_seconds=1,
    )
    runtime_fixture.clock.monotonic_values = [0.0, 0.0, 0.0, 2.0]
    runtime_fixture.provider.outcomes = [ProviderFinal("result:late")]

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.PROVIDER_TIMEOUT
    assert runtime_fixture.control.final_state is ExecutionState.FAILED


def test_action_timeout_is_distinct_from_cancellation(runtime_fixture):
    runtime_fixture.provider.outcomes = [
        ProviderToolRequest(action_ref="tool:slow", invocation_ref="invocation:slow"),
        ProviderFinal("result:after-action"),
    ]
    runtime_fixture.action.raise_timeout = True

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.ACTION_TIMEOUT
    assert runtime_fixture.control.final_state is ExecutionState.FAILED


def test_action_timeout_budget_is_checked_after_action_effect(runtime_fixture):
    runtime_fixture.runtime._limits = RuntimeLimits(
        max_duration_seconds=60,
        action_timeout_seconds=1,
    )
    runtime_fixture.provider.outcomes = [
        ProviderToolRequest(action_ref="tool:slow", invocation_ref="invocation:slow"),
        ProviderFinal("result:after-action"),
    ]
    runtime_fixture.clock.monotonic_values = [0.0, 0.0, 0.0, 0.0, 0.0, 2.0]
    runtime_fixture.action.outcomes = [ActionSucceeded("result:slow")]

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.ACTION_TIMEOUT
    assert runtime_fixture.control.final_state is ExecutionState.FAILED


def test_cancel_during_action_prevents_resume(runtime_fixture):
    runtime_fixture.provider.outcomes = [
        ProviderToolRequest(action_ref="tool:cancel", invocation_ref="invocation:cancel")
    ]
    runtime_fixture.action.on_call = lambda _request: setattr(
        runtime_fixture.control, "signal", ControlSignal.CANCEL_REQUESTED
    )

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, CancelledOutcome)
    assert runtime_fixture.control.final_state is ExecutionState.CANCELLED
    assert ExecutionState.RUNNING not in runtime_fixture.control.targets[3:]


def test_context_failure_is_terminal(runtime_fixture):
    runtime_fixture.context.fail_assembly = True

    context_outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(context_outcome, FailedOutcome)
    assert context_outcome.error.category is RuntimeErrorCategory.CONTEXT

    assert runtime_fixture.control.final_state is ExecutionState.FAILED


def test_model_failure_is_terminal(runtime_fixture):
    runtime_fixture.resolver.fail = True
    model_outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(model_outcome, FailedOutcome)
    assert model_outcome.error.category is RuntimeErrorCategory.MODEL_RESOLUTION


def test_action_failure_is_not_reported_as_success(runtime_fixture):
    runtime_fixture.provider.outcomes = [
        ProviderToolRequest(action_ref="tool:fail", invocation_ref="invocation:fail")
    ]
    runtime_fixture.action.outcomes = [
        ActionFailed(RuntimeErrorInfo(RuntimeErrorCategory.ACTION, "ACTION_REJECTED"))
    ]

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.ACTION
    assert runtime_fixture.control.final_state is ExecutionState.FAILED


def test_action_failure_commit_uncertainty_is_reported_as_reconciliation(runtime_fixture):
    runtime_fixture.provider.outcomes = [
        ProviderToolRequest(action_ref="tool:fail", invocation_ref="invocation:fail")
    ]
    runtime_fixture.action.outcomes = [
        ActionFailed(RuntimeErrorInfo(RuntimeErrorCategory.ACTION, "ACTION_REJECTED"))
    ]
    original_commit = runtime_fixture.control.commit
    commit_calls = 0

    def commit_with_uncertainty(command):
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            return Indeterminate("transaction:unknown")
        return original_commit(command)

    runtime_fixture.control.commit = commit_with_uncertainty

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.RECONCILIATION
    assert runtime_fixture.control.final_state is ExecutionState.WAITING_TOOL


def test_runtime_rejects_a_second_concurrent_execution(runtime_fixture):
    started = Event()
    release = Event()

    def block_provider(_request):
        started.set()
        release.wait(timeout=2)

    runtime_fixture.provider.on_call = block_provider
    results = []
    worker = Thread(
        target=lambda: results.append(
            runtime_fixture.runtime.execute(runtime_fixture.request)
        )
    )
    worker.start()
    assert started.wait(timeout=2)

    concurrent = runtime_fixture.runtime.execute(runtime_fixture.request)
    release.set()
    worker.join(timeout=2)

    assert isinstance(concurrent, FailedOutcome)
    assert concurrent.error.category is RuntimeErrorCategory.CONCURRENCY
    assert len(results) == 1


def test_late_reexecution_of_terminal_execution_does_not_call_provider(runtime_fixture):
    first = runtime_fixture.runtime.execute(runtime_fixture.request)
    calls_after_first = len(runtime_fixture.provider.calls)

    second = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(first, CompletedOutcome)
    assert isinstance(second, CompletedOutcome)
    assert len(runtime_fixture.provider.calls) == calls_after_first


def test_ownership_mismatch_returns_sanitized_failure(runtime_fixture):
    runtime_fixture.request = replace(runtime_fixture.request, user_id="other-user")

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.INITIALIZATION
    assert outcome.error.code == "EXECUTION_ACCESS_DENIED"
