from __future__ import annotations

from datetime import datetime, timezone

from agentos.context.compat import RuntimeContextManagerAdapter
from agentos.runtime.models import (
    ContextAssemblyRequest as RuntimeContextAssemblyRequest,
    OperationContext,
    TaskReference,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def runtime_request():
    return RuntimeContextAssemblyRequest(
        context=OperationContext(
            user_id="user-1",
            workspace_id="workspace-1",
            agent_id="agent-1",
            execution_id="execution-1",
            correlation_id="correlation-1",
            purpose="context-test",
            actor_ref="actor-1",
        ),
        turn=1,
        task_ref=TaskReference("task:1"),
        model_requirements_ref="requirements:1",
    )


def test_runtime_adapter_preserves_complete_operation_context(context_fixture):
    adapter = RuntimeContextManagerAdapter(context_fixture.manager)
    result = adapter.assemble(runtime_request())
    assert result.context_ref
    assert result.manifest_ref
    assert context_fixture.source.queries[0].context.user_id == "user-1"
    assert context_fixture.source.queries[0].context.workspace_id == "workspace-1"
    assert context_fixture.source.queries[0].context.agent_id == "agent-1"
    assert context_fixture.source.queries[0].context.execution_id == "execution-1"
    assert context_fixture.source.queries[0].context.correlation_id == "correlation-1"
    assert context_fixture.source.queries[0].context.purpose == "context-test"


def test_runtime_adapter_does_not_expose_canonical_items_or_payloads(context_fixture):
    result = RuntimeContextManagerAdapter(context_fixture.manager).assemble(runtime_request())
    assert result.context_ref
    assert result.manifest_ref
    assert "prompt" not in repr(result).lower()
