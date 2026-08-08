from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from agentos.api.contracts import ApplicationConflictError, ApplicationNotFoundError
from agentos.persistence.postgres import upgrade
from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter, ExecutionQueryAdapter


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


def _engine():
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    return engine


def _create_command(execution_id: str, user_id: str, agent_id: str) -> dict:
    return {
        "operation_id": f"op_{uuid4().hex}",
        "context": {"user_id": user_id, "workspace_id": None, "agent_id": agent_id, "execution_id": execution_id},
        "task_ref": "task-ref-1",
        "limits": {},
        "expected_agent_version": None,
        "idempotency_key": f"idem_{uuid4().hex}",
        "requested_at": datetime.now(UTC),
    }


def test_create_then_get_round_trips_through_real_postgres() -> None:
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    application = ExecutionApplicationAdapter(engine)
    query = ExecutionQueryAdapter(engine)

    receipt = application.create(_create_command(execution_id, user_id, "agent-1"))
    view = query.get({"resource_id": execution_id, "user_id": user_id, "purpose": "executions.read"})

    assert receipt == {"outcome": "accepted", "execution_id": execution_id, "state_version": 1}
    assert view["execution_id"] == execution_id
    assert view["state"] == "QUEUED"
    assert view["state_version"] == 1
    assert view["result"] is None
    assert view["failure"] is None


def test_control_cancel_resolves_agent_id_without_client_supplying_it() -> None:
    # PAUSE/RESUME only apply to a RUNNING/PAUSED execution, which is reached
    # only through the internal Runtime's acquire/transition path, not through
    # the public control API. CANCEL from QUEUED is the one control action
    # reachable immediately after create, so it is what this test exercises.
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    application = ExecutionApplicationAdapter(engine)
    application.create(_create_command(execution_id, user_id, "agent-1"))

    receipt = application.control({
        "operation_id": f"op_{uuid4().hex}",
        "context": {"user_id": user_id, "execution_id": execution_id},
        "action": "CANCEL",
        "expected_state_version": 1,
        "reason": "user_requested",
        "idempotency_key": f"idem_{uuid4().hex}",
        "requested_at": datetime.now(UTC),
    })

    assert receipt["outcome"] == "accepted"
    view = ExecutionQueryAdapter(engine).get({"resource_id": execution_id, "user_id": user_id, "purpose": "executions.read"})
    assert view["state"] == "CANCELLED"
    assert view["state_version"] == 2


def test_control_with_stale_expected_version_raises_conflict() -> None:
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    user_id = f"user:{uuid4().hex}"
    application = ExecutionApplicationAdapter(engine)
    application.create(_create_command(execution_id, user_id, "agent-1"))

    with pytest.raises(ApplicationConflictError):
        application.control({
            "operation_id": f"op_{uuid4().hex}",
            "context": {"user_id": user_id, "execution_id": execution_id},
            "action": "PAUSE",
            "expected_state_version": 99,
            "reason": "user_requested",
            "idempotency_key": f"idem_{uuid4().hex}",
            "requested_at": datetime.now(UTC),
        })


def test_get_on_someone_elses_execution_is_not_found_not_leaked() -> None:
    engine = _engine()
    execution_id = f"exe_{uuid4().hex}"
    owner_id = f"user:{uuid4().hex}"
    stranger_id = f"user:{uuid4().hex}"
    application = ExecutionApplicationAdapter(engine)
    application.create(_create_command(execution_id, owner_id, "agent-1"))

    with pytest.raises(ApplicationNotFoundError):
        ExecutionQueryAdapter(engine).get({"resource_id": execution_id, "user_id": stranger_id, "purpose": "executions.read"})


def test_list_returns_only_the_callers_own_executions() -> None:
    engine = _engine()
    user_id = f"user:{uuid4().hex}"
    other_id = f"user:{uuid4().hex}"
    execution_id = f"exe_{uuid4().hex}"
    application = ExecutionApplicationAdapter(engine)
    application.create(_create_command(execution_id, user_id, "agent-1"))
    application.create(_create_command(f"exe_{uuid4().hex}", other_id, "agent-1"))

    result = ExecutionQueryAdapter(engine).list({"user_id": user_id, "purpose": "executions.read", "cursor": None})

    assert [item["execution_id"] for item in result["items"]] == [execution_id]
