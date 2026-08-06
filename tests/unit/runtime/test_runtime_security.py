from __future__ import annotations

from pathlib import Path

from agentos.runtime.models import CompletedOutcome


def test_all_sensitive_port_requests_preserve_execution_context(runtime_fixture):
    runtime_fixture.runtime.execute(runtime_fixture.request)

    assert runtime_fixture.context.assembly_calls[0].context.user_id == "user-1"
    assert runtime_fixture.context.assembly_calls[0].context.workspace_id == "workspace-1"
    assert runtime_fixture.context.assembly_calls[0].context.agent_id == "agent-1"
    assert runtime_fixture.context.assembly_calls[0].context.execution_id == "execution-1"
    assert runtime_fixture.context.assembly_calls[0].context.correlation_id == "correlation-1"
    assert runtime_fixture.context.assembly_calls[0].context.purpose == "runtime-test"
    assert runtime_fixture.resolver.calls[0].context == runtime_fixture.context.assembly_calls[0].context
    assert runtime_fixture.provider.calls[0].context == runtime_fixture.context.assembly_calls[0].context
    assert any(
        change.kind == "context-manifest-recorded"
        and change.reference == "manifest:1"
        for change in runtime_fixture.control.changes
    )


def test_runtime_has_no_event_publisher_or_persistence_dependency():
    source = Path("src/agentos/runtime/service.py").read_text()

    assert "TransactionalPersistence" not in source
    assert "EventBus" not in source
    assert "publish(" not in source


def test_final_outcome_contains_reference_not_sensitive_payload(runtime_fixture):
    outcome = runtime_fixture.runtime.execute(runtime_fixture.request)

    assert isinstance(outcome, CompletedOutcome)
    assert "prompt" not in repr(outcome).lower()
    assert "secret" not in repr(outcome).lower()
