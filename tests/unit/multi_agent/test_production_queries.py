from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agentos.events import DataClassification
from agentos.multi_agent import (
    AgentMessage,
    AgentMessageKind,
    Collaboration,
    CollaborationPolicy,
    InMemoryMultiAgentStore,
    SendAgentMessage,
)
from agentos.multi_agent.production import DurableMultiAgentStore
from agentos.multi_agent.queries import (
    AuthorizedAgentIdentityQuery,
    MultiAgentQueryService,
)
from agentos.persistence import InMemoryTransactionalPersistence


NOW = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)


def _collaboration(*, user_id="user:1", workspace_id="workspace:1", owner="owner:1"):
    return Collaboration(
        collaboration_id="collab:1",
        user_id=user_id,
        workspace_id=workspace_id,
        owner=owner,
        participant_agent_ids=("agent:source", "agent:target"),
        coordinator_agent_id="agent:source",
        policy=CollaborationPolicy(4, ("task.delegation",), DataClassification.CONFIDENTIAL),
        correlation_id="corr:1",
        created_at=NOW,
        version=1,
    )


def _message(**overrides):
    values = dict(
        actor="owner:1",
        collaboration_id="collab:1",
        sender_agent_id="agent:source",
        recipient_agent_id="agent:target",
        user_id="user:1",
        workspace_id="workspace:1",
        owner="owner:1",
        kind=AgentMessageKind.INFORM,
        purpose="task.delegation",
        classification=DataClassification.INTERNAL,
        inline_summary="bounded summary",
        content_refs=("artifact:opaque",),
        handoff_ref=None,
        deadline_at=None,
        correlation_id="corr:1",
        causation_id="command:1",
        idempotency_key="message:1",
        requested_at=NOW,
    )
    values.update(overrides)
    return SendAgentMessage(**values)


def test_durable_store_enforces_scope_at_read_time():
    store = DurableMultiAgentStore(InMemoryTransactionalPersistence())
    store.save_collaboration(_collaboration(), idempotency_key="collab:1")

    with pytest.raises(PermissionError):
        store.get_collaboration("collab:1", "user:2", "workspace:1")
    with pytest.raises(PermissionError):
        store.get_collaboration("collab:1", "user:1", "workspace:other")


def test_query_projection_returns_bounded_summary_without_private_content():
    store = DurableMultiAgentStore(InMemoryTransactionalPersistence())
    store.save_collaboration(_collaboration(), idempotency_key="collab:1")
    message_command = _message()
    store.save_message(
        AgentMessage(
            message_id="message:1",
            collaboration_id="collab:1",
            sender_agent_id="agent:source",
            recipient_agent_id="agent:target",
            user_id="user:1",
            workspace_id="workspace:1",
            owner="owner:1",
            purpose="task.delegation",
            classification=DataClassification.INTERNAL,
            correlation_id="corr:1",
            causation_id="command:1",
            delivery_execution_id="delivery:1",
            kind=AgentMessageKind.INFORM,
            inline_summary="bounded summary",
            content_refs=("artifact:opaque",),
            handoff_ref=None,
            deadline_at=None,
            idempotency_key="message:1",
            created_at=NOW,
        ),
        fingerprint="fingerprint:1",
    )

    page = MultiAgentQueryService(store).list_messages(
        AuthorizedAgentIdentityQuery(
            user_id="user:1",
            workspace_id="workspace:1",
            actor="owner:1",
            purpose="task.delegation",
        )
    )

    assert page.items[0].summary == "bounded summary"
    assert not hasattr(page.items[0], "content")
    assert page.items[0].content_refs == ("artifact:opaque",)
    assert MultiAgentQueryService(store).list_messages(
        AuthorizedAgentIdentityQuery(
            user_id="user:2",
            workspace_id="workspace:1",
            actor="owner:2",
            purpose="task.delegation",
        )
    ).items == ()


def test_identity_query_rejects_private_prompt_like_summary():
    with pytest.raises(ValueError, match="protected"):
        AgentMessage(
            message_id="message:private",
            collaboration_id="collab:1",
            sender_agent_id="agent:source",
            recipient_agent_id="agent:target",
            user_id="user:1",
            workspace_id="workspace:1",
            owner="owner:1",
            purpose="task.delegation",
            classification=DataClassification.INTERNAL,
            correlation_id="corr:1",
            causation_id="command:1",
            delivery_execution_id="delivery:private",
            kind=AgentMessageKind.INFORM,
            inline_summary="password=do-not-return",
            content_refs=(),
            handoff_ref=None,
            deadline_at=None,
            idempotency_key="message:private",
            created_at=NOW,
        )
