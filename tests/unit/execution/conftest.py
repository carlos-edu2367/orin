from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentos.execution.control import ExecutionControlService
from agentos.execution.in_memory import InMemoryTransactionalPersistence
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
from agentos.execution.ports import ExecutionCommandContext


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def command_context() -> ExecutionCommandContext:
    return ExecutionCommandContext(
        user_id="user-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        execution_id="execution-1",
        correlation_id="correlation-1",
        purpose="unit-test",
    )


def make_execution(
    *,
    now: datetime,
    state: ExecutionState = ExecutionState.QUEUED,
    version: int = 1,
) -> Execution:
    return Execution(
        execution_id="execution-1",
        ownership=Ownership(user_id="user-1", workspace_id="workspace-1"),
        agent_id="agent-1",
        task=TaskSnapshot(task_ref="task-1", revision=1),
        state=state,
        state_version=version,
        correlation_id="correlation-1",
        causation_id=None,
        parent_execution_id=None,
        context_manifest_ref=None,
        result=ExecutionResult(result_ref="result-1") if state is ExecutionState.COMPLETED else None,
        failure=ExecutionFailure(code=FailureReason.RUNTIME_ERROR) if state is ExecutionState.FAILED else None,
        cancellation_reason=(
            CancellationReason(code=CancellationReasonCode.USER_REQUESTED)
            if state is ExecutionState.CANCELLED
            else None
        ),
        limits=ExecutionLimits(max_duration_seconds=60, max_iterations=10),
        usage=ExecutionUsage(),
        iteration_count=0,
        created_at=now,
        queued_at=now,
        started_at=None,
        updated_at=now,
        finished_at=now if state in {
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
        } else None,
    )


@pytest.fixture
def make_control(now):
    def factory(state=ExecutionState.QUEUED, version=1):
        persistence = InMemoryTransactionalPersistence()
        persistence.seed(make_execution(now=now, state=state, version=version))
        return ExecutionControlService(persistence), persistence

    return factory
