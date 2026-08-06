from __future__ import annotations

import pytest

from agentos.runtime.models import (
    CompletedOutcome,
    RuntimeRequest,
    RuntimeUsage,
)


def test_runtime_request_rejects_blank_sensitive_fields():
    with pytest.raises(ValueError):
        RuntimeRequest(
            execution_id="execution-1",
            user_id="user-1",
            workspace_id="workspace-1",
            agent_id="agent-1",
            actor_ref="actor-1",
            worker_ref="worker-1",
            correlation_id="correlation-1",
            purpose="",
            model_requirements_ref="requirements-1",
        )


def test_outcomes_are_reference_only():
    outcome = CompletedOutcome(
        execution_id="execution-1",
        result_ref="result-1",
        usage=RuntimeUsage(iterations=1),
    )

    assert outcome.result_ref == "result-1"
    assert "secret" not in repr(outcome).lower()
