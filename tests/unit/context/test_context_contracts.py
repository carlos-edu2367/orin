from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentos.context.models import (
    ContextAssemblyRequest,
    ContextBudget,
    ContextOperationContext,
    ContextSnapshot,
    TaskSnapshot,
    TokenAccounting,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def test_assembly_request_requires_complete_sensitive_scope():
    with pytest.raises(ValueError):
        ContextAssemblyRequest(
            context=ContextOperationContext(
                user_id="user-1",
                workspace_id="workspace-1",
                agent_id="agent-1",
                execution_id="execution-1",
                correlation_id="correlation-1",
                purpose="",
            ),
            turn=1,
            task=TaskSnapshot(reference="task:1", content="do work"),
            model_requirements_ref="requirements:1",
            budget=ContextBudget(maximum_input_tokens=100),
        )


def test_context_budget_rejects_negative_reservations():
    with pytest.raises(ValueError):
        ContextBudget(maximum_input_tokens=100, reserved_control_tokens=-1)


def test_snapshot_is_reference_focused():
    snapshot = ContextSnapshot(
        execution_id="execution-1",
        turn=1,
        items=(),
        token_accounting=TokenAccounting(),
        context_ref="context:1",
        manifest_ref="manifest:1",
        assembled_at=NOW,
    )

    assert snapshot.manifest_ref == "manifest:1"
    assert "secret" not in repr(snapshot).lower()
