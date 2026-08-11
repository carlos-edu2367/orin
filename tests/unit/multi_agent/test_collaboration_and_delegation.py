from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agentos.events import DataClassification
from agentos.multi_agent import (
    AgentMessageKind,
    Collaboration,
    CollaborationPolicy,
    DelegateTask,
    DelegationCancellationPolicy,
    DelegationFailurePolicy,
    DelegationResult,
    DelegationTerminalState,
    InMemoryMultiAgentStore,
    MultiAgentCoordinatorService,
    ReturnDelegationResult,
    SendAgentMessage,
)
from agentos.execution.models import ExecutionLimits


NOW = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)


def _collaboration() -> Collaboration:
    return Collaboration(
        collaboration_id="collab:1",
        user_id="user:1",
        workspace_id="workspace:1",
        owner="actor:1",
        participant_agent_ids=("agent:source", "agent:target"),
        coordinator_agent_id="agent:source",
        policy=CollaborationPolicy(4, ("task.delegation",), DataClassification.CONFIDENTIAL),
        correlation_id="corr:1",
        created_at=NOW,
        version=1,
    )


class FakeResolver:
    def __init__(self, inactive=None, providers=None):
        self.inactive = inactive
        self.providers = providers or {}
        self.calls = []

    def resolve(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["agent_id"] == self.inactive:
            raise ValueError("agent is not active")
        provider = self.providers.get(kwargs["agent_id"], "openrouter")
        return SimpleNamespace(agent_id=kwargs["agent_id"], config_version=2, model_profile_ref=f"model-profile:{provider}:model-a:version")


class FakeExecution:
    def __init__(self):
        self.deliveries = []
        self.children = []

    def create_delivery(self, **kwargs):
        self.deliveries.append(kwargs)
        return SimpleNamespace(execution_id=kwargs["execution_id"], state_version=1)

    def create_child(self, **kwargs):
        self.children.append(kwargs)
        return SimpleNamespace(execution_id=kwargs["execution_id"], state_version=1)


class FakeSharing:
    def __init__(self):
        self.calls = []

    def resolve_handoff(self, ref, **kwargs):
        self.calls.append((ref, kwargs))
        return SimpleNamespace(handoff_id=ref.handoff_id)


def _service(*, inactive=None, providers=None, model_policy=None):
    store = InMemoryMultiAgentStore()
    store.save_collaboration(_collaboration(), idempotency_key="collab:1")
    execution = FakeExecution()
    resolver = FakeResolver(inactive=inactive, providers=providers)
    sharing = FakeSharing()
    service = MultiAgentCoordinatorService(
        store=store,
        resolver=resolver,
        administration=SimpleNamespace(request_create=lambda command: command),
        execution=execution,
        sharing=sharing,
        events=store,
        clock=lambda: NOW,
        model_policy=model_policy,
    )
    return service, store, execution, resolver, sharing


def _send(**overrides):
    values = dict(
        actor="actor:1",
        collaboration_id="collab:1",
        sender_agent_id="agent:source",
        recipient_agent_id="agent:target",
        user_id="user:1",
        workspace_id="workspace:1",
        owner="actor:1",
        kind=AgentMessageKind.INFORM,
        purpose="task.delegation",
        classification=DataClassification.INTERNAL,
        inline_summary="bounded",
        content_refs=(),
        handoff_ref=None,
        deadline_at=None,
        correlation_id="corr:1",
        causation_id="command:1",
        idempotency_key="message:1",
        requested_at=NOW,
    )
    values.update(overrides)
    return SendAgentMessage(**values)


def test_send_creates_message_and_distinct_delivery_execution():
    service, store, execution, resolver, _ = _service()
    receipt = service.send(_send())
    assert receipt.message_id.startswith("message:")
    assert receipt.delivery_execution_id.startswith("delivery:")
    assert len(execution.deliveries) == 1
    assert execution.deliveries[0]["request"].kind is AgentMessageKind.INFORM
    assert any(event.event_type == "AgentMessageCreated" for event in store.events)


def test_send_is_idempotent_and_conflicting_fingerprint_is_rejected():
    service, _, execution, _, _ = _service()
    first = service.send(_send())
    second = service.send(_send())
    assert second == first
    assert len(execution.deliveries) == 1
    with pytest.raises(ValueError, match="idempotency"):
        service.send(_send(inline_summary="different"))


def test_send_rejects_cross_workspace_and_inactive_recipient():
    service, _, _, _, _ = _service(inactive="agent:target")
    with pytest.raises(PermissionError):
        service.send(_send(workspace_id="workspace:other"))
    with pytest.raises(ValueError, match="active"):
        service.send(_send())


def test_delegate_resolves_handoff_and_creates_one_child_execution():
    service, store, execution, resolver, sharing = _service()
    ref = SimpleNamespace(
        handoff_id="handoff:1", version=1, to_agent_id="agent:target",
        target_execution_id="execution:child", expires_at=NOW,
    )
    command = DelegateTask(
        actor="actor:1", collaboration_id="collab:1", parent_execution_id="execution:parent",
        delegator_agent_id="agent:source", delegate_agent_id="agent:target", user_id="user:1",
        workspace_id="workspace:1", owner="actor:1", handoff_ref=ref,
        child_limits=ExecutionLimits(60, 5), deadline_at=None, purpose="task.delegation",
        classification=DataClassification.INTERNAL, authorization_ref="auth:1",
        failure_policy=DelegationFailurePolicy.PROPAGATE,
        cancellation_policy=DelegationCancellationPolicy.CANCEL_CHILD_ONLY,
        correlation_id="corr:1", causation_id="command:delegate", idempotency_key="delegate:1",
        requested_at=NOW,
    )
    receipt = service.delegate(command)
    assert receipt.child_execution_id.startswith("execution:child:")
    assert len(execution.children) == 1
    assert len(sharing.calls) == 1
    assert execution.children[0]["request"].parent_execution_id == "execution:parent"
    assert any(event.event_type == "DelegationCreated" for event in store.events)


def test_delegate_keeps_child_on_same_provider_by_default_and_rejects_cross_provider_without_grant():
    service, _, execution, _, _ = _service(providers={"agent:source": "openrouter", "agent:target": "openrouter"})
    ref = SimpleNamespace(handoff_id="handoff:provider", version=1, to_agent_id="agent:target", target_execution_id="execution:child", expires_at=NOW)
    command = DelegateTask("actor:1", "collab:1", "execution:parent", "agent:source", "agent:target", "user:1", "workspace:1", "actor:1", ref, ExecutionLimits(60, 5), None, "task.delegation", DataClassification.INTERNAL, "auth:1", DelegationFailurePolicy.PROPAGATE, DelegationCancellationPolicy.CANCEL_CHILD_ONLY, "corr:1", "command:provider", "delegate:provider", NOW)
    service.delegate(command)
    assert execution.children[0]["resolved_agent"].model_profile_ref.startswith("model-profile:openrouter:")

    policy = SimpleNamespace(validate=lambda **_: (_ for _ in ()).throw(PermissionError("cross-provider child model requires an explicit grant")))
    cross, _, _, _, _ = _service(providers={"agent:source": "openrouter", "agent:target": "openai"}, model_policy=policy)
    with pytest.raises(PermissionError, match="cross-provider"):
        cross.delegate(command)


def test_delegate_accepts_cross_provider_only_when_explicit_grant_authorizes_it():
    policy = SimpleNamespace(validate=lambda **_: None)
    service, _, execution, _, _ = _service(providers={"agent:source": "openrouter", "agent:target": "openai"}, model_policy=policy)
    ref = SimpleNamespace(handoff_id="handoff:grant", version=1, to_agent_id="agent:target", target_execution_id="execution:child", expires_at=NOW)
    command = DelegateTask("actor:1", "collab:1", "execution:parent", "agent:source", "agent:target", "user:1", "workspace:1", "actor:1", ref, ExecutionLimits(60, 5), None, "task.delegation", DataClassification.INTERNAL, "provider-grant:approved", DelegationFailurePolicy.PROPAGATE, DelegationCancellationPolicy.CANCEL_CHILD_ONLY, "corr:1", "command:grant", "delegate:grant", NOW)
    service.delegate(command)
    assert len(execution.children) == 1




def test_return_result_keeps_failed_terminal_distinct():
    service, store, _, _, _ = _service()
    ref = SimpleNamespace(handoff_id="handoff:1", version=1, to_agent_id="agent:target", target_execution_id="execution:child", expires_at=NOW)
    command = DelegateTask(
        actor="actor:1", collaboration_id="collab:1", parent_execution_id="execution:parent",
        delegator_agent_id="agent:source", delegate_agent_id="agent:target", user_id="user:1",
        workspace_id="workspace:1", owner="actor:1", handoff_ref=ref,
        child_limits=ExecutionLimits(60, 5), deadline_at=None, purpose="task.delegation",
        classification=DataClassification.INTERNAL, authorization_ref="auth:1",
        failure_policy=DelegationFailurePolicy.CONTINUE_WITH_FAILURE_REF,
        cancellation_policy=DelegationCancellationPolicy.CANCEL_CHILD_ONLY,
        correlation_id="corr:1", causation_id="command:delegate", idempotency_key="delegate:1",
        requested_at=NOW,
    )
    delegation = service.delegate(command)
    result = DelegationResult(
        delegation_id=delegation.delegation_id,
        child_execution_id=delegation.child_execution_id,
        terminal_state=DelegationTerminalState.FAILED,
        result_ref=None,
        failure_ref="failure:1",
        handback_ref=None,
        finished_at=NOW,
        failure_policy=DelegationFailurePolicy.CONTINUE_WITH_FAILURE_REF,
    )
    receipt = service.return_result(ReturnDelegationResult(
        actor="actor:1", delegation_id=delegation.delegation_id, result=result,
        user_id="user:1", workspace_id="workspace:1", purpose="task.delegation",
        correlation_id="corr:1", idempotency_key="return:1", requested_at=NOW,
    ))
    assert receipt.result.terminal_state is DelegationTerminalState.FAILED
    event_types = {event.event_type for event in store.events}
    assert "DelegationFailed" in event_types
    assert "DelegationResultReturned" in event_types
