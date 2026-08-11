"""Authorized, sanitized read projections for the multi-agent surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import base64
import json

from agentos.agents import AgentAccessContext, AgentPageCursor, AuthorizedAgentQuery
from agentos.events import DataClassification


@dataclass(frozen=True, slots=True)
class AuthorizedAgentIdentityQuery:
    user_id: str
    workspace_id: str | None
    actor: str
    purpose: str
    limit: int = 50
    cursor: str | None = None
    classification: DataClassification = DataClassification.INTERNAL

    def __post_init__(self) -> None:
        if not self.user_id or not self.actor or not self.purpose:
            raise ValueError("query scope is required")
        if self.limit < 1 or self.limit > 100:
            raise ValueError("limit must be between one and one hundred")
        object.__setattr__(self, "classification", DataClassification(self.classification))


@dataclass(frozen=True, slots=True)
class QueryPage:
    items: tuple
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AgentIdentityView:
    agent_id: str
    owner: str
    display_name: str
    workspace_id: str | None
    state: str
    config_version: int


@dataclass(frozen=True, slots=True)
class AgentProfileView(AgentIdentityView):
    model_profile_ref: str
    prompt_ref: str
    prompt_version: int
    private_memory_scope_ref: str


@dataclass(frozen=True, slots=True)
class CollaborationView:
    collaboration_id: str
    owner: str
    user_id: str
    workspace_id: str | None
    participant_agent_ids: tuple[str, ...]
    coordinator_agent_id: str | None
    state: str
    version: int


@dataclass(frozen=True, slots=True)
class DelegationView:
    delegation_id: str
    parent_execution_id: str
    child_execution_id: str
    delegator_agent_id: str
    delegate_agent_id: str
    state: str
    purpose: str
    deadline_at: datetime | None


@dataclass(frozen=True, slots=True)
class AgentMessageView:
    message_id: str
    collaboration_id: str
    sender_agent_id: str
    recipient_agent_id: str
    kind: str
    summary: str | None
    content_refs: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WaitView:
    wait_id: str
    waiting_execution_id: str
    checkpoint_ref: str


class MultiAgentQueryService:
    def __init__(self, store, *, agent_registry=None) -> None:
        self.store = store
        self.agent_registry = agent_registry

    def list_agents(self, query: AuthorizedAgentIdentityQuery) -> QueryPage:
        if self.agent_registry is None:
            return QueryPage(())
        page = self.agent_registry.list(
            AuthorizedAgentQuery(
                user_id=query.user_id,
                workspace_id=query.workspace_id,
                actor=query.actor,
                purpose=query.purpose,
                limit=query.limit,
                cursor=AgentPageCursor(query.cursor) if query.cursor else None,
                classification=query.classification,
            )
        )
        return QueryPage(tuple(self._identity(snapshot) for snapshot in page.items), str(page.next_cursor) if page.next_cursor else None)

    def get_agent_profile(self, query: AuthorizedAgentIdentityQuery, agent_id: str) -> AgentProfileView:
        if self.agent_registry is None:
            raise PermissionError("agent not found")
        snapshot = self.agent_registry.get(
            agent_id,
            AgentAccessContext(query.user_id, query.workspace_id, query.actor, query.purpose),
        )
        if snapshot.agent.owner != query.actor:
            raise PermissionError("agent not found")
        identity = self._identity(snapshot)
        return AgentProfileView(
            agent_id=identity.agent_id,
            owner=identity.owner,
            display_name=identity.display_name,
            workspace_id=identity.workspace_id,
            state=identity.state,
            config_version=identity.config_version,
            model_profile_ref=str(snapshot.configuration.model_profile_ref),
            prompt_ref=str(snapshot.configuration.prompt.prompt_ref),
            prompt_version=int(snapshot.configuration.prompt.prompt_version),
            private_memory_scope_ref=str(snapshot.agent.private_memory_scope.scope_ref),
        )

    def list_collaborations(self, query: AuthorizedAgentIdentityQuery) -> QueryPage:
        return self._page(
            tuple(self._collaboration(item) for item in self._records("collaboration", query) if item.owner == query.actor),
            query,
        )

    def list_delegations(self, query: AuthorizedAgentIdentityQuery) -> QueryPage:
        return self._page(
            tuple(self._delegation(item) for item in self._records("delegation", query) if item.owner == query.actor),
            query,
        )

    def list_messages(self, query: AuthorizedAgentIdentityQuery) -> QueryPage:
        return self._page(
            tuple(self._message(item) for item in self._records("message", query) if item.owner == query.actor),
            query,
        )

    def list_waits(self, query: AuthorizedAgentIdentityQuery) -> QueryPage:
        return self._page(
            tuple(self._wait(item) for item in self._records("wait", query)),
            query,
        )

    @staticmethod
    def _identity(snapshot):
        return AgentIdentityView(
            agent_id=str(snapshot.agent.agent_id),
            owner=snapshot.agent.owner,
            display_name=snapshot.agent.display_name,
            workspace_id=snapshot.agent.workspace_id,
            state=snapshot.agent.administrative_state.value,
            config_version=int(snapshot.configuration.config_version),
        )

    @staticmethod
    def _collaboration(item):
        return CollaborationView(
            collaboration_id=item.collaboration_id,
            owner=item.owner,
            user_id=item.user_id,
            workspace_id=item.workspace_id,
            participant_agent_ids=tuple(item.participant_agent_ids),
            coordinator_agent_id=item.coordinator_agent_id,
            state=item.state.value,
            version=item.version,
        )

    @staticmethod
    def _delegation(item):
        return DelegationView(
            delegation_id=item.delegation_id,
            parent_execution_id=item.parent_execution_id,
            child_execution_id=item.child_execution_id,
            delegator_agent_id=item.delegator_agent_id,
            delegate_agent_id=item.delegate_agent_id,
            state="CREATED",
            purpose=item.purpose,
            deadline_at=item.deadline_at,
        )

    @staticmethod
    def _message(item):
        return AgentMessageView(
            message_id=item.message_id,
            collaboration_id=item.collaboration_id,
            sender_agent_id=item.sender_agent_id,
            recipient_agent_id=item.recipient_agent_id,
            kind=item.kind.value,
            summary=_sanitize_summary(item.inline_summary),
            content_refs=tuple(item.content_refs),
            created_at=item.created_at,
        )

    @staticmethod
    def _wait(item):
        return WaitView(item.wait_id, item.waiting_execution_id, item.checkpoint_ref)

    def _records(self, kind: str, query: AuthorizedAgentIdentityQuery):
        try:
            return self.store.list_records(kind, query.user_id, query.workspace_id, query.actor)
        except TypeError:
            return self.store.list_records(kind, query.user_id, query.workspace_id)

    @staticmethod
    def _page(items, query):
        ordered = tuple(items)
        offset = _decode_cursor(query.cursor)
        selected = ordered[offset : offset + query.limit]
        next_cursor = _encode_cursor(offset + query.limit) if offset + query.limit < len(ordered) else None
        return QueryPage(tuple(selected), next_cursor)


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"offset": offset}, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode()))
        offset = value["offset"]
        if not isinstance(offset, int) or offset < 0:
            raise ValueError
        return offset
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid query cursor") from exc


def _sanitize_summary(summary: str | None) -> str | None:
    if summary is None:
        return None
    normalized = " ".join(summary.split())[:160]
    protected = ("secret", "password", "token", "credential", "api_key", "prompt")
    return "[redacted]" if any(word in normalized.lower() for word in protected) else normalized


__all__ = [
    "AgentIdentityView", "AgentMessageView", "AgentProfileView", "AuthorizedAgentIdentityQuery",
    "CollaborationView", "DelegationView", "MultiAgentQueryService", "QueryPage", "WaitView",
]
