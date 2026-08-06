from datetime import datetime, timezone

import pytest

from agentos.events import DataClassification
from agentos.multi_agent import (
    AgentMessage,
    AgentMessageKind,
    CancelDelegation,
    CancellationScope,
    Collaboration,
    CollaborationPolicy,
    CompletionRule,
    DelegationFailurePolicy,
    DelegationResult,
    DelegationTerminalState,
    WaitForDelegations,
    fingerprint,
)


NOW = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)


def test_collaboration_and_message_contracts_are_frozen_and_bounded():
    collaboration = Collaboration(
        collaboration_id="collab:1",
        user_id="user:1",
        workspace_id="workspace:1",
        owner="actor:1",
        participant_agent_ids=("agent:source", "agent:target"),
        coordinator_agent_id="agent:source",
        policy=CollaborationPolicy(
            maximum_participants=4,
            allowed_purposes=("task.delegation",),
            classification_ceiling=DataClassification.CONFIDENTIAL,
        ),
        correlation_id="corr:1",
        created_at=NOW,
        version=1,
    )
    message = AgentMessage(
        message_id="message:1",
        collaboration_id=collaboration.collaboration_id,
        sender_agent_id="agent:source",
        recipient_agent_id="agent:target",
        user_id="user:1",
        workspace_id="workspace:1",
        owner="actor:1",
        purpose="task.delegation",
        classification=DataClassification.INTERNAL,
        correlation_id="corr:1",
        causation_id="command:1",
        delivery_execution_id="execution:delivery",
        kind=AgentMessageKind.INFORM,
        inline_summary="bounded summary",
        content_refs=("content:1",),
        handoff_ref=None,
        deadline_at=None,
        idempotency_key="message-key:1",
        created_at=NOW,
    )
    assert message.content_refs == ("content:1",)
    with pytest.raises(AttributeError):
        message.kind = AgentMessageKind.REQUEST


def test_wait_requires_bounded_minimum_count_and_matching_rule():
    with pytest.raises(ValueError, match="minimum_count"):
        WaitForDelegations(
            actor="actor:1",
            user_id="user:1",
            workspace_id="workspace:1",
            waiting_execution_id="execution:parent",
            delegation_ids=("delegation:1",),
            completion_rule=CompletionRule.MINIMUM_COUNT,
            minimum_count=None,
            deadline_at=None,
            purpose="task.delegation",
            correlation_id="corr:1",
            idempotency_key="wait:1",
            requested_at=NOW,
        )


def test_terminal_result_allows_only_matching_success_or_failure_reference():
    result = DelegationResult(
        delegation_id="delegation:1",
        child_execution_id="execution:child",
        terminal_state=DelegationTerminalState.FAILED,
        result_ref=None,
        failure_ref="failure:1",
        handback_ref=None,
        finished_at=NOW,
        failure_policy=DelegationFailurePolicy.CONTINUE_WITH_FAILURE_REF,
    )
    assert result.failure_ref == "failure:1"
    with pytest.raises(ValueError, match="COMPLETED"):
        DelegationResult(
            delegation_id="delegation:1",
            child_execution_id="execution:child",
            terminal_state=DelegationTerminalState.COMPLETED,
            result_ref=None,
            failure_ref=None,
            handback_ref=None,
            finished_at=NOW,
            failure_policy=DelegationFailurePolicy.PROPAGATE,
        )


def test_cancel_scope_and_fingerprint_are_explicit_and_stable():
    command = CancelDelegation(
        actor="actor:1",
        user_id="user:1",
        workspace_id="workspace:1",
        delegation_id="delegation:1",
        target=CancellationScope.SUBTREE,
        purpose="task.delegation",
        correlation_id="corr:1",
        idempotency_key="cancel:1",
        requested_at=NOW,
    )
    assert command.target is CancellationScope.SUBTREE
    assert fingerprint(command) == fingerprint(command)
