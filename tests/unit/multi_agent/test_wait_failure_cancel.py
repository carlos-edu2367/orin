from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from agentos.events import DataClassification
from agentos.execution.models import ExecutionLimits
from agentos.multi_agent import (
    CancelDelegation,
    CancellationScope,
    CompletionRule,
    DelegateTask,
    DelegationCancellationPolicy,
    DelegationFailurePolicy,
    DelegationResult,
    DelegationTerminalState,
    SendAgentMessage,
    WaitForDelegations,
)
from .test_support import build_service


NOW = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)


def _delegate(service, key="delegate:1", failure_policy=DelegationFailurePolicy.PROPAGATE):
    return service.delegate(DelegateTask(
        actor="actor:1", collaboration_id="collab:1", parent_execution_id="execution:parent",
        delegator_agent_id="agent:source", delegate_agent_id="agent:target", user_id="user:1",
        workspace_id="workspace:1", owner="actor:1",
        handoff_ref=SimpleNamespace(handoff_id="handoff:1", version=1, to_agent_id="agent:target", target_execution_id="execution:child", expires_at=NOW),
        child_limits=ExecutionLimits(60, 5), deadline_at=None, purpose="task.delegation",
        classification=DataClassification.INTERNAL, authorization_ref="auth:1",
        failure_policy=failure_policy, cancellation_policy=DelegationCancellationPolicy.CASCADE,
        correlation_id="corr:1", causation_id="command:delegate", idempotency_key=key,
        requested_at=NOW,
    ))


def _wait(delegation_id, rule=CompletionRule.ALL, **overrides):
    values = dict(
        actor="actor:1", user_id="user:1", workspace_id="workspace:1",
        waiting_execution_id="execution:parent", delegation_ids=(delegation_id,),
        completion_rule=rule, minimum_count=None, deadline_at=None,
        purpose="task.delegation", correlation_id="corr:1", idempotency_key="wait:1",
        requested_at=NOW, expected_version=1, checkpoint_ref="checkpoint:1",
    )
    values.update(overrides)
    return WaitForDelegations(**values)


def test_wait_pauses_then_resumes_parent_only_after_terminal_child():
    service, store, execution, _, _ = build_service()
    delegation = _delegate(service)
    receipt = service.wait_for(_wait(delegation.delegation_id))
    assert receipt.checkpoint_ref == "checkpoint:1"
    assert execution.pauses == ["execution:parent"]
    assert service.reconcile_wait(receipt.wait_id, ()) is False
    result = DelegationResult(
        delegation_id=delegation.delegation_id, child_execution_id=delegation.child_execution_id,
        terminal_state=DelegationTerminalState.COMPLETED, result_ref="result:1", failure_ref=None,
        handback_ref=None, finished_at=NOW, failure_policy=DelegationFailurePolicy.PROPAGATE,
    )
    assert service.reconcile_wait(receipt.wait_id, (result,)) is True
    assert execution.resumes == ["execution:parent"]
    assert any(event.event_type == "AgentWaitSatisfied" for event in store.events)


def test_wait_deadline_does_not_resume_from_late_result():
    service, _, execution, _, _ = build_service()
    delegation = _delegate(service)
    receipt = service.wait_for(_wait(delegation.delegation_id, deadline_at=NOW + timedelta(minutes=1)))
    late = DelegationResult(
        delegation_id=delegation.delegation_id, child_execution_id=delegation.child_execution_id,
        terminal_state=DelegationTerminalState.COMPLETED, result_ref="result:late", failure_ref=None,
        handback_ref=None, finished_at=NOW + timedelta(minutes=2), failure_policy=DelegationFailurePolicy.PROPAGATE,
    )
    assert service.reconcile_wait(receipt.wait_id, (late,), now=NOW + timedelta(minutes=2)) is False
    assert execution.resumes == []


def test_cancel_child_uses_execution_control_and_returns_target():
    service, _, execution, _, _ = build_service()
    delegation = _delegate(service)
    receipt = service.request_cancel(CancelDelegation(
        actor="actor:1", user_id="user:1", workspace_id="workspace:1",
        delegation_id=delegation.delegation_id, target=CancellationScope.CHILD,
        purpose="task.delegation", correlation_id="corr:1", idempotency_key="cancel:1", requested_at=NOW,
    ))
    assert receipt.requested_execution_ids == (delegation.child_execution_id,)
    assert execution.cancellations == [delegation.child_execution_id]


def test_retry_is_a_new_delegation_attempt_and_execution_id():
    service, _, execution, _, _ = build_service()
    first = _delegate(service, "delegate:1", DelegationFailurePolicy.REQUEST_RETRY)
    second = _delegate(service, "delegate:2", DelegationFailurePolicy.REQUEST_RETRY)
    assert second.delegation_id != first.delegation_id
    assert second.child_execution_id != first.child_execution_id
    assert len(execution.children) == 2
