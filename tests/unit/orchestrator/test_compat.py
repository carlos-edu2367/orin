from datetime import datetime, timezone
from types import SimpleNamespace

from agentos.execution.models import ExecutionLimits, Ownership, TaskSnapshot
from agentos.execution.ports import Accepted, ExecutionCommandContext
from agentos.orchestrator.compat import (
    AgentAdministrationAdapter,
    ExecutionCancellationAdapter,
    ExecutionControlExecutionFactory,
)
from agentos.orchestrator.models import CreateExecutionRequest


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


class Control:
    def __init__(self):
        self.created = []
        self.cancelled = []

    def create(self, command):
        self.created.append(command)
        return Accepted(1, "tx:1")

    def request_cancel(self, command):
        self.cancelled.append(command)
        return Accepted(2, "tx:2")


def creation_request(key="execution:1"):
    return CreateExecutionRequest(
        ownership=Ownership("user:1", None),
        agent_id="agent:1",
        agent_config_version=3,
        task=TaskSnapshot("task:1", 1),
        limits=ExecutionLimits(60, 5),
        correlation_id="correlation:1",
        purpose="orchestrator.execute",
        idempotency_key=key,
        requested_at=NOW,
    )


def test_execution_factory_uses_execution_control_and_fixes_config_version():
    control = Control()
    factory = ExecutionControlExecutionFactory(control, execution_id_factory=lambda: "execution:1")
    result = factory.create(creation_request())
    assert result.execution_id == "execution:1"
    command = control.created[0]
    assert command.execution.state.value == "QUEUED"
    assert command.execution.agent_config_version == 3
    assert command.context.execution_id == "execution:1"


def test_cancellation_adapter_sends_expected_version_to_kernel():
    control = Control()
    adapter = ExecutionCancellationAdapter(control)
    result = adapter.cancel(
        execution_id="execution:1",
        ownership=Ownership("user:1", None),
        agent_id="agent:1",
        correlation_id="correlation:1",
        purpose="orchestrator.execute",
        actor="actor:1",
        idempotency_key="cancel:1",
        expected_version=4,
        requested_at=NOW,
    )
    assert result.resulting_version == 2
    assert control.cancelled[0].expected_version == 4


def test_agent_administration_adapter_delegates_by_public_command_type():
    class Administration:
        def request_suspend(self, command):
            return "execution:admin"

    adapter = AgentAdministrationAdapter(Administration())
    command = SimpleNamespace(__class__=object)
    command = SimpleNamespace()
    result = adapter.request(command, operation="suspend")
    assert result == "execution:admin"

