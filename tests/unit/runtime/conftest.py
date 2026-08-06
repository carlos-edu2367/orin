from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from agentos.execution.models import (
    CancellationReason,
    CancellationReasonCode,
    Execution,
    ExecutionFailure,
    ExecutionLimits,
    ExecutionResult,
    ExecutionState,
    ExecutionUsage,
    FailureReason,
    Ownership,
    TaskSnapshot,
)
from agentos.execution.ports import (
    Accepted,
    AlreadyApplied,
    ControlSignal,
    ExecutionCommandContext,
    Rejected,
)
from agentos.runtime.models import (
    ActionSucceeded,
    BudgetDecision,
    BudgetEvaluation,
    ContextSnapshot,
    ModelSelection,
    ProviderFinal,
    RuntimeRequest,
    RuntimeUsage,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def make_execution(state: ExecutionState = ExecutionState.QUEUED) -> Execution:
    return Execution(
        execution_id="execution-1",
        ownership=Ownership(user_id="user-1", workspace_id="workspace-1"),
        agent_id="agent-1",
        task=TaskSnapshot(task_ref="task-1", revision=1),
        state=state,
        state_version=1,
        correlation_id="correlation-1",
        causation_id=None,
        parent_execution_id=None,
        context_manifest_ref=None,
        result=ExecutionResult("result:existing") if state is ExecutionState.COMPLETED else None,
        failure=ExecutionFailure(FailureReason.RUNTIME_ERROR) if state is ExecutionState.FAILED else None,
        cancellation_reason=(
            CancellationReason(CancellationReasonCode.USER_REQUESTED)
            if state is ExecutionState.CANCELLED
            else None
        ),
        limits=ExecutionLimits(
            max_duration_seconds=60,
            max_iterations=10,
            max_cost=Decimal("10"),
            max_provider_tokens=1000,
        ),
        usage=ExecutionUsage(),
        iteration_count=0,
        created_at=NOW,
        queued_at=NOW,
        started_at=NOW if state not in {ExecutionState.QUEUED} else None,
        updated_at=NOW,
        finished_at=NOW
        if state in {ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.CANCELLED}
        else None,
    )


class FakeControl:
    def __init__(self, execution: Execution) -> None:
        self.execution = execution
        self.signal = ControlSignal.CONTINUE
        self.targets: list[ExecutionState] = []
        self.changes = []
        self.final_state = execution.state

    def load(self, context: ExecutionCommandContext) -> Execution:
        if (
            context.execution_id != self.execution.execution_id
            or context.user_id != self.execution.ownership.user_id
            or context.workspace_id != self.execution.ownership.workspace_id
            or context.agent_id != self.execution.agent_id
            or context.correlation_id != self.execution.correlation_id
        ):
            raise PermissionError("unauthorized")
        return self.execution

    def current_signal(self, query):
        return self.signal

    def acquire(self, command):
        if self.execution.state is not ExecutionState.QUEUED:
            return Rejected(reason="INVALID_TRANSITION", current_state=self.execution.state)
        self._move(ExecutionState.STARTING)
        return Accepted(self.execution.state_version)

    def transition(self, command):
        self._move(command.target_state, command)
        return Accepted(self.execution.state_version)

    def request_cancel(self, command):
        self._move(ExecutionState.CANCELLED, command)
        return Accepted(self.execution.state_version)

    def request_pause(self, command):
        self._move(ExecutionState.PAUSED, command)
        return Accepted(self.execution.state_version)

    def commit(self, command):
        self.changes.extend(command.changes)
        self._move(command.target_state, command)
        return Accepted(self.execution.state_version)

    def _move(self, target, command=None):
        self.targets.append(target)
        self.final_state = target
        now = getattr(command, "requested_at", NOW)
        changes = getattr(command, "changes", ()) if command is not None else ()
        self.execution = replace(
            self.execution,
            state=target,
            state_version=self.execution.state_version + 1,
            started_at=now if target is ExecutionState.RUNNING and self.execution.started_at is None else self.execution.started_at,
            finished_at=now if target in {ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.CANCELLED} else None,
            result=ExecutionResult(command.result_ref) if target is ExecutionState.COMPLETED else None,
            failure=command.failure if target is ExecutionState.FAILED else None,
            cancellation_reason=(
                getattr(command, "cancellation_reason", None) or getattr(command, "reason", None)
                if target is ExecutionState.CANCELLED
                else None
            ),
            updated_at=now,
            usage=replace(
                self.execution.usage,
                duration_seconds=self.execution.usage.duration_seconds
                + sum(change.duration_seconds for change in changes),
                iterations=self.execution.usage.iterations
                + sum(change.iterations for change in changes),
                provider_tokens=self.execution.usage.provider_tokens
                + sum(change.provider_tokens for change in changes),
                cost=self.execution.usage.cost
                + sum((change.cost for change in changes), Decimal("0")),
            ),
            iteration_count=self.execution.iteration_count
            + sum(change.iterations for change in changes),
        )


class FakeContext:
    def __init__(self):
        self.assembly_calls = []
        self.turn_calls = []
        self.finalized = []
        self.fail_assembly = False
        self.fail_turn = False

    def assemble(self, request):
        self.assembly_calls.append(request)
        if self.fail_assembly:
            raise RuntimeError("context failure")
        return ContextSnapshot("context:1", "manifest:1")

    def apply_turn(self, request):
        self.turn_calls.append(request)
        if self.fail_turn:
            raise RuntimeError("context turn failure")
        return ContextSnapshot("context:2", "manifest:2")

    def finalize(self, execution_id, disposition):
        self.finalized.append((execution_id, disposition))


class FakeResolver:
    def __init__(self):
        self.calls = []
        self.fail = False

    def resolve(self, request):
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("model resolution failure")
        return ModelSelection("selection:1", "approved:1")


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.outcomes = [ProviderFinal("result:1", RuntimeUsage(iterations=1))]
        self.on_call = None

    def generate(self, request):
        self.calls.append(request)
        if self.on_call is not None:
            self.on_call(request)
        return self.outcomes.pop(0)


class FakeAction:
    def __init__(self):
        self.calls = []
        self.outcomes = []
        self.raise_timeout = False
        self.on_call = None

    def invoke(self, request):
        self.calls.append(request.action_ref)
        if self.on_call is not None:
            self.on_call(request)
        if self.raise_timeout:
            raise TimeoutError("action timeout")
        if self.outcomes:
            return self.outcomes.pop(0)
        return ActionSucceeded("action-result:1")


class FakeCheckpoint:
    def __init__(self):
        self.calls = []
        self.snapshot = None
        self.raise_error = False

    def load(self, checkpoint_ref, context):
        self.calls.append((checkpoint_ref, context))
        if self.raise_error:
            raise RuntimeError("checkpoint failure")
        if self.snapshot is None:
            raise AssertionError("checkpoint not configured")
        return self.snapshot

    def latest_safe(self, execution_id, context):
        return None


class FakeClock:
    def __init__(self):
        self.monotonic_value = 0.0
        self.monotonic_values = None

    def now(self):
        return NOW

    def monotonic(self):
        if self.monotonic_values:
            return self.monotonic_values.pop(0)
        return self.monotonic_value


class FakeBudget:
    def evaluate(self, request):
        return BudgetEvaluation(BudgetDecision.CONTINUE)


@pytest.fixture
def runtime_fixture():
    from agentos.runtime.service import RuntimeService

    control = FakeControl(make_execution())
    context = FakeContext()
    resolver = FakeResolver()
    provider = FakeProvider()
    action = FakeAction()
    checkpoints = FakeCheckpoint()
    clock = FakeClock()
    request = RuntimeRequest(
        execution_id="execution-1",
        user_id="user-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        actor_ref="actor-1",
        worker_ref="worker-1",
        correlation_id="correlation-1",
        purpose="runtime-test",
        model_requirements_ref="requirements:1",
        requested_at=NOW,
    )
    runtime = RuntimeService(
        control=control,
        context_manager=context,
        model_resolver=resolver,
        provider=provider,
        action_port=action,
        checkpoint_port=checkpoints,
        clock=clock,
        budget_policy=FakeBudget(),
    )
    return SimpleNamespace(
        runtime=runtime,
        request=request,
        control=control,
        context=context,
        resolver=resolver,
        provider=provider,
        action=action,
        checkpoints=checkpoints,
        clock=clock,
    )
