from __future__ import annotations

from dataclasses import replace

from agentos.execution.models import ExecutionState
from tests.unit.runtime.conftest import make_execution
from agentos.runtime.models import (
    CheckpointSnapshot,
    ContextReference,
    FailedOutcome,
    ProviderFailed,
    Retryability,
    RuntimeErrorCategory,
    RuntimeErrorInfo,
    RuntimeLimits,
)


def test_provider_timeout_is_failed_as_timeout_and_terminal(runtime_fixture):
    runtime_fixture.provider.outcomes = [
        ProviderFailed(
            RuntimeErrorInfo(
                category=RuntimeErrorCategory.PROVIDER_TIMEOUT,
                code="PROVIDER_DEADLINE_EXCEEDED",
                retryability=Retryability.SAFE,
            )
        )
    ]

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.PROVIDER_TIMEOUT
    assert runtime_fixture.control.final_state is ExecutionState.FAILED


def test_iteration_limit_prevents_provider_effect(runtime_fixture):
    runtime_fixture.runtime._limits = RuntimeLimits(max_iterations=0)

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.category is RuntimeErrorCategory.LIMIT
    assert runtime_fixture.provider.calls == []
    assert runtime_fixture.control.final_state is ExecutionState.FAILED


def test_recovery_loads_checkpoint_before_provider(runtime_fixture):
    runtime_fixture.request = replace(runtime_fixture.request, resume_from="checkpoint:1")
    runtime_fixture.checkpoints.snapshot = CheckpointSnapshot(
        checkpoint_ref="checkpoint:1",
        execution_id="execution-1",
        state_version=1,
        iteration=1,
        context_manifest_ref=ContextReference("manifest:1"),
    )

    runtime_fixture.runtime.execute(runtime_fixture.request)

    assert len(runtime_fixture.checkpoints.calls) == 1
    assert runtime_fixture.checkpoints.calls[0][0] == "checkpoint:1"


def test_recovery_does_not_repeat_confirmed_action(runtime_fixture):
    runtime_fixture.control.execution = make_execution(ExecutionState.WAITING_TOOL)
    runtime_fixture.request = replace(runtime_fixture.request, resume_from="checkpoint:1")
    runtime_fixture.checkpoints.snapshot = CheckpointSnapshot(
        checkpoint_ref="checkpoint:1",
        execution_id="execution-1",
        state_version=1,
        iteration=1,
        context_manifest_ref=ContextReference("manifest:1"),
        pending_action_ref=None,
    )

    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert outcome.result_ref == "result:1"
    assert runtime_fixture.action.calls == []
    assert runtime_fixture.control.final_state is ExecutionState.COMPLETED
