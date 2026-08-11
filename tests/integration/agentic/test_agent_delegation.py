from datetime import datetime, timezone
from types import SimpleNamespace

from agentos.context import HandoffRef
from agentos.events import DataClassification
from agentos.execution.models import ExecutionLimits
from agentos.multi_agent import (
    Collaboration,
    CollaborationPolicy,
    DelegationCancellationPolicy,
    DelegationFailurePolicy,
    DelegationResult,
    DelegationTerminalState,
    DelegateTask,
    InMemoryMultiAgentStore,
    MultiAgentCoordinatorService,
    ReturnDelegationResult,
    WaitForDelegations,
    CompletionRule,
)


NOW = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)


class Resolver:
    def resolve(self, **kwargs):
        return SimpleNamespace(
            agent_id=kwargs["agent_id"],
            config_version=1,
            model_profile_ref="model-profile:opaque",
        )


class Execution:
    def __init__(self):
        self.children = []
        self.pauses = []
        self.resumes = []

    def create_child(self, **kwargs):
        self.children.append(kwargs)
        return SimpleNamespace(resulting_version=2)

    def request_pause(self, context, **kwargs):
        self.pauses.append((context, kwargs))
        return SimpleNamespace(resulting_version=7)

    def request_resume(self, context, **kwargs):
        self.resumes.append((context, kwargs))
        return SimpleNamespace(resulting_version=8)

    def request_cancel(self, context, **kwargs):
        return SimpleNamespace(resulting_version=9)


class Sharing:
    def resolve_handoff(self, ref, **kwargs):
        return SimpleNamespace(handoff_id=ref.handoff_id)


def _service():
    store = InMemoryMultiAgentStore()
    store.save_collaboration(
        Collaboration(
            collaboration_id="collab:1",
            user_id="user:1",
            workspace_id="workspace:1",
            owner="owner:1",
            participant_agent_ids=("agent:source", "agent:target"),
            coordinator_agent_id="agent:source",
            policy=CollaborationPolicy(4, ("task.delegation",), DataClassification.CONFIDENTIAL),
            correlation_id="corr:1",
            created_at=NOW,
            version=1,
        ),
        idempotency_key="collab:1",
    )
    execution = Execution()
    service = MultiAgentCoordinatorService(
        store=store,
        resolver=Resolver(),
        administration=SimpleNamespace(request_create=lambda command: command),
        execution=execution,
        sharing=Sharing(),
        events=store,
        clock=lambda: NOW,
    )
    return service, store, execution


def _delegate():
    return DelegateTask(
        actor="owner:1",
        collaboration_id="collab:1",
        parent_execution_id="execution:parent",
        delegator_agent_id="agent:source",
        delegate_agent_id="agent:target",
        user_id="user:1",
        workspace_id="workspace:1",
        owner="owner:1",
        handoff_ref=HandoffRef(
            handoff_id="handoff:1",
            grant_id="grant:1",
            from_agent_id="agent:source",
            to_agent_id="agent:target",
            source_execution_id="execution:parent",
            target_execution_id="execution:child",
            purpose="task.delegation",
            classification=DataClassification.INTERNAL,
            version=1,
            expires_at=NOW,
            integrity_ref="integrity:1",
        ),
        child_limits=ExecutionLimits(60, 5),
        deadline_at=None,
        purpose="task.delegation",
        classification=DataClassification.INTERNAL,
        authorization_ref="auth:1",
        failure_policy=DelegationFailurePolicy.CONTINUE_WITH_FAILURE_REF,
        cancellation_policy=DelegationCancellationPolicy.CANCEL_CHILD_ONLY,
        correlation_id="corr:1",
        causation_id="command:delegate",
        idempotency_key="delegate:1",
        requested_at=NOW,
    )


def test_parent_wait_resumes_once_after_child_result_and_never_exposes_private_content():
    service, store, execution = _service()
    delegation = service.delegate(_delegate())
    wait = service.wait_for(
        WaitForDelegations(
            actor="owner:1",
            user_id="user:1",
            workspace_id="workspace:1",
            waiting_execution_id="execution:parent",
            delegation_ids=(delegation.delegation_id,),
            completion_rule=CompletionRule.ALL,
            minimum_count=None,
            deadline_at=None,
            purpose="task.delegation",
            correlation_id="corr:1",
            idempotency_key="wait:1",
            requested_at=NOW,
        )
    )

    service.return_result(
        ReturnDelegationResult(
            actor="owner:1",
            delegation_id=delegation.delegation_id,
            result=DelegationResult(
                delegation_id=delegation.delegation_id,
                child_execution_id=delegation.child_execution_id,
                terminal_state=DelegationTerminalState.COMPLETED,
                result_ref="result:opaque",
                failure_ref=None,
                handback_ref=None,
                finished_at=NOW,
                failure_policy=DelegationFailurePolicy.CONTINUE_WITH_FAILURE_REF,
            ),
            user_id="user:1",
            workspace_id="workspace:1",
            purpose="task.delegation",
            correlation_id="corr:1",
            idempotency_key="return:1",
            requested_at=NOW,
        )
    )

    assert wait.waiting_execution_id == "execution:parent"
    assert len(execution.children) == 1
    assert len(execution.pauses) == 1
    assert len(execution.resumes) == 1
    assert execution.resumes[0][1]["expected_version"] == 7
    assert all("private" not in str(event.payload).lower() for event in store.events)
