from datetime import datetime, timezone
from types import SimpleNamespace

from agentos.agents.ports import AdministrativeExecutionRef
from agentos.execution.ports import Accepted, CreateExecution
from agentos.multi_agent import DelegateTask, DelegationCancellationPolicy, DelegationFailurePolicy
from agentos.multi_agent.compat import (
    AgentAdministrationAdapter,
    AgentResolverAdapter,
    ExecutionControlAdapter,
)


NOW = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)


def test_agent_resolver_adapter_revalidates_through_public_registry():
    class Registry:
        def __init__(self):
            self.request = None

        def resolve_for_execution(self, request):
            self.request = request
            return "resolved-agent"

    registry = Registry()
    adapter = AgentResolverAdapter(registry)
    assert adapter.resolve(
        agent_id="agent:target",
        user_id="user:1",
        workspace_id="workspace:1",
        purpose="task.delegation",
        correlation_id="corr:1",
        actor="actor:1",
        classification="INTERNAL",
    ) == "resolved-agent"
    assert registry.request.actor == "actor:1"


def test_execution_control_adapter_creates_child_with_new_id_and_parent_relation():
    class Control:
        def __init__(self):
            self.commands = []

        def create(self, command):
            self.commands.append(command)
            return Accepted(resulting_version=1, transaction_id="tx:1")

    control = Control()
    adapter = ExecutionControlAdapter(control)
    request = DelegateTask(
        actor="actor:1",
        collaboration_id="collab:1",
        parent_execution_id="execution:parent",
        delegator_agent_id="agent:source",
        delegate_agent_id="agent:target",
        user_id="user:1",
        workspace_id="workspace:1",
        owner="actor:1",
        handoff_ref=SimpleNamespace(handoff_id="handoff:1", version=1),
        child_limits=SimpleNamespace(max_duration_seconds=60, max_iterations=5, max_cost=None, max_provider_tokens=None),
        deadline_at=None,
        purpose="task.delegation",
        classification="INTERNAL",
        authorization_ref="auth:1",
        failure_policy=DelegationFailurePolicy.PROPAGATE,
        cancellation_policy=DelegationCancellationPolicy.CANCEL_CHILD_ONLY,
        correlation_id="corr:1",
        causation_id="command:1",
        idempotency_key="delegation:1",
        requested_at=NOW,
    )
    resolved = SimpleNamespace(config_version=3)
    receipt = adapter.create_child(request=request, execution_id="execution:child", resolved_agent=resolved)
    command = control.commands[0]
    assert receipt.execution_id == "execution:child"
    assert isinstance(command, CreateExecution)
    assert command.execution.parent_execution_id == "execution:parent"
    assert command.execution.execution_id == "execution:child"
    assert command.execution.agent_config_version == 3
    try:
        receipt.execution_id = "forged"
    except AttributeError:
        pass
    else:
        raise AssertionError("execution receipt must be immutable")


def test_agent_administration_adapter_uses_explicit_creation_port():
    class Administration:
        def request_create(self, command):
            return AdministrativeExecutionRef("execution:admin", command.correlation_id, command.idempotency_key)

    adapter = AgentAdministrationAdapter(Administration())
    command = SimpleNamespace(correlation_id="corr:1", idempotency_key="create:1")
    assert adapter.request_create(command).execution_id == "execution:admin"
