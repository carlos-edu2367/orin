from datetime import UTC, datetime, timedelta

import pytest

from agentos.workers.models import (
    DispatchAttempt,
    DispatchAttemptState,
    WorkerOperationContext,
    WorkerPool,
    WorkItem,
    WorkKind,
    destination_pool_for,
)


def test_closed_work_kind_mapping_rejects_a_browser_item_in_agent_pool() -> None:
    assert destination_pool_for(WorkKind.BROWSER_ACTION) is WorkerPool.BROWSER

    context = WorkerOperationContext(
        user_id="user-1", workspace_id="workspace-1", agent_id="agent-1",
        execution_id="execution-1", correlation_id="correlation-1", purpose="run", actor="worker",
    )
    with pytest.raises(ValueError, match="work_kind"):
        WorkItem(
            work_item_id="item-1", dispatch_id="dispatch-1", dispatch_attempt_id="attempt-1",
            execution_id="execution-1", context=context, pool=WorkerPool.AGENT,
            work_kind=WorkKind.BROWSER_ACTION, payload_ref="payload-1", expected_execution_version=1,
            not_before=datetime.now(UTC), expires_at=datetime.now(UTC) + timedelta(minutes=5),
            attempt_number=1, attempt_limit=3, timeout=timedelta(minutes=1), idempotency_key="key-1",
        )


def test_attempt_transition_requires_the_current_lease_and_fence() -> None:
    attempt = DispatchAttempt.enqueued("attempt-1", "dispatch-1", 1)
    leased = attempt.acquire("lease-1", worker_id="worker-1", fence=4, expires_at=datetime.now(UTC))

    with pytest.raises(ValueError, match="fencing"):
        leased.acknowledge("lease-1", fence=3)

    acknowledged = leased.acknowledge("lease-1", fence=4)
    assert acknowledged.state is DispatchAttemptState.ACKNOWLEDGED
    assert acknowledged.version == 3
