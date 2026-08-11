"""Port-backed Agent identity and profile adapters.

This module uses the canonical persistence port rather than reaching into a
database.  The record is intentionally reference-first: prompt text,
credentials, private memory and grant contents are never serialized.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from agentos.events import DataClassification
from agentos.persistence import (
    AuthorizedRead,
    AuthorizedScan,
    AuthorizedRecord,
    ConsistencyLevel,
    PageRequest,
    PersistenceOperationContext,
    RecordChange,
    RecordReference,
    TransactionOptions,
    TransactionRequest,
)

from .models import (
    Agent,
    AgentAdministrativeState,
    AgentConfiguration,
    AgentPresentation,
    AgentSnapshot,
    AgentConfigVersion,
    MemoryScopeReference,
    OpaqueAgentReference,
    PromptSpecification,
    ResolvedAgent,
    ResolvedAgentPolicies,
    WorkspaceAssignment,
)
from .ports import (
    AgentAccessContext,
    AgentAdministration,
    AgentIdempotencyConflict,
    AgentNotFound,
    AgentPage,
    AgentPageCursor,
    AgentResolutionRejected,
    AgentResolutionRequest,
    AgentTransactionReceipt,
    AgentTransactionRequest,
    AgentTransactionResult,
    AgentTransactionalPersistence,
    AdministrativeExecutionRef,
    AuthorizedAgentQuery,
)
from .security import classification_allows


_RECORD_TYPE = "agent.identity"
_ACTOR = "agent-store"
_AGENT = "agent-store"
_EXECUTION = "agent-store"
_CORRELATION = "agent-store"
_PURPOSE = "agents.persist"


def _context(user_id: str, workspace_id: str | None) -> PersistenceOperationContext:
    return PersistenceOperationContext(
        user_id=user_id,
        workspace_id=workspace_id,
        agent_id=_AGENT,
        execution_id=_EXECUTION,
        correlation_id=_CORRELATION,
        purpose=_PURPOSE,
        actor=_ACTOR,
    )


def _ref(value: OpaqueAgentReference | str) -> str:
    return str(value)


def _serialize(snapshot: AgentSnapshot, event=None) -> dict[str, object]:
    agent = snapshot.agent
    configuration = snapshot.configuration
    return {
        "agent": {
            "agent_id": str(agent.agent_id), "user_id": str(agent.user_id), "workspace_id": agent.workspace_id,
            "owner": agent.owner, "display_name": agent.display_name,
            "administrative_state": agent.administrative_state.value, "current_config_version": int(agent.current_config_version),
            "private_memory_scope": {
                "scope_ref": _ref(agent.private_memory_scope.scope_ref), "user_id": str(agent.private_memory_scope.user_id),
                "agent_id": str(agent.private_memory_scope.agent_id), "workspace_id": agent.private_memory_scope.workspace_id,
                "classification": agent.private_memory_scope.classification.value,
                "provenance_ref": _ref(agent.private_memory_scope.provenance_ref),
                "retention_policy_ref": _ref(agent.private_memory_scope.retention_policy_ref),
            },
            "created_by": agent.created_by, "created_at": agent.created_at.isoformat(), "updated_at": agent.updated_at.isoformat(),
            "suspended_at": agent.suspended_at.isoformat() if agent.suspended_at else None,
            "archived_at": agent.archived_at.isoformat() if agent.archived_at else None,
            "audit_refs": [_ref(item) for item in agent.audit_refs],
        },
        "configuration": {
            "agent_id": str(configuration.agent_id), "config_version": int(configuration.config_version),
            "model_profile_ref": _ref(configuration.model_profile_ref),
            "prompt": {"prompt_ref": _ref(configuration.prompt.prompt_ref), "prompt_version": configuration.prompt.prompt_version, "instruction_classification": configuration.prompt.instruction_classification.value},
            "presentation": {"avatar_ref": _ref(configuration.presentation.avatar_ref) if configuration.presentation.avatar_ref else None, "color": configuration.presentation.color},
            "tool_grants": [_ref(item) for item in configuration.tool_grants],
            "capability_grants": [_ref(item) for item in configuration.capability_grants],
            "skill_grants": [_ref(item) for item in configuration.skill_grants],
            "execution_policy_ref": _ref(configuration.execution_policy_ref), "context_policy_ref": _ref(configuration.context_policy_ref),
            "memory_policy_ref": _ref(configuration.memory_policy_ref),
            "workspace_assignments": [{"workspace_id": str(item.workspace_id), "assignment_ref": _ref(item.assignment_ref), "assigned_by": item.assigned_by, "assigned_at": item.assigned_at.isoformat()} for item in configuration.workspace_assignments],
            "created_by": configuration.created_by, "created_at": configuration.created_at.isoformat(),
            "supersedes_version": int(configuration.supersedes_version) if configuration.supersedes_version else None,
        },
        "event_id": event.event_id if event is not None else None,
    }


def _deserialize(data: dict[str, object]) -> AgentSnapshot:
    raw_agent = data["agent"]
    raw_config = data["configuration"]
    memory = raw_agent["private_memory_scope"]
    private_memory = MemoryScopeReference(
        scope_ref=OpaqueAgentReference(memory["scope_ref"]), user_id=memory["user_id"], agent_id=memory["agent_id"], workspace_id=memory["workspace_id"],
        classification=DataClassification(memory["classification"]), provenance_ref=OpaqueAgentReference(memory["provenance_ref"]), retention_policy_ref=OpaqueAgentReference(memory["retention_policy_ref"]),
    )
    agent = Agent(
        agent_id=raw_agent["agent_id"], user_id=raw_agent["user_id"], workspace_id=raw_agent["workspace_id"], owner=raw_agent["owner"], display_name=raw_agent["display_name"],
        administrative_state=AgentAdministrativeState(raw_agent["administrative_state"]), current_config_version=raw_agent["current_config_version"], private_memory_scope=private_memory,
        created_by=raw_agent["created_by"], created_at=datetime.fromisoformat(raw_agent["created_at"]), updated_at=datetime.fromisoformat(raw_agent["updated_at"]),
        suspended_at=datetime.fromisoformat(raw_agent["suspended_at"]) if raw_agent["suspended_at"] else None,
        archived_at=datetime.fromisoformat(raw_agent["archived_at"]) if raw_agent["archived_at"] else None,
        audit_refs=tuple(OpaqueAgentReference(item) for item in raw_agent["audit_refs"]),
    )
    prompt = raw_config["prompt"]
    presentation = raw_config["presentation"]
    configuration = AgentConfiguration(
        agent_id=raw_config["agent_id"], config_version=raw_config["config_version"], model_profile_ref=OpaqueAgentReference(raw_config["model_profile_ref"]),
        prompt=PromptSpecification(OpaqueAgentReference(prompt["prompt_ref"]), prompt["prompt_version"], DataClassification(prompt["instruction_classification"])),
        presentation=AgentPresentation(OpaqueAgentReference(presentation["avatar_ref"]) if presentation["avatar_ref"] else None, presentation["color"]),
        tool_grants=tuple(OpaqueAgentReference(item) for item in raw_config["tool_grants"]),
        capability_grants=tuple(OpaqueAgentReference(item) for item in raw_config["capability_grants"]),
        skill_grants=tuple(OpaqueAgentReference(item) for item in raw_config["skill_grants"]),
        execution_policy_ref=OpaqueAgentReference(raw_config["execution_policy_ref"]), context_policy_ref=OpaqueAgentReference(raw_config["context_policy_ref"]), memory_policy_ref=OpaqueAgentReference(raw_config["memory_policy_ref"]),
        workspace_assignments=tuple(WorkspaceAssignment(item["workspace_id"], OpaqueAgentReference(item["assignment_ref"]), item["assigned_by"], datetime.fromisoformat(item["assigned_at"])) for item in raw_config["workspace_assignments"]),
        created_by=raw_config["created_by"], created_at=datetime.fromisoformat(raw_config["created_at"]), supersedes_version=raw_config["supersedes_version"],
    )
    return AgentSnapshot(agent, configuration)


class DurableAgentTransactionalPersistence(AgentTransactionalPersistence):
    """Agent persistence backed by the public RFC 601 transactional port."""

    def __init__(self, persistence, *, event_recorder=None) -> None:
        self.persistence = persistence
        self.event_recorder = event_recorder

    def get_snapshot(self, agent_id, user_id, workspace_id, config_version=None):
        record = self.persistence.read(
            AuthorizedRead(_context(user_id, workspace_id), RecordReference(f"agent:{agent_id}"), _RECORD_TYPE, DataClassification.RESTRICTED)
        )
        if not isinstance(record, AuthorizedRecord):
            return None
        snapshot = _deserialize(record.data)
        if config_version is not None and int(snapshot.configuration.config_version) != int(config_version):
            return None
        return snapshot

    def scan(self, user_id, workspace_id):
        page = self.persistence.scan(
            AuthorizedScan(_context(user_id, workspace_id), _RECORD_TYPE, {}, DataClassification.RESTRICTED, PageRequest(limit=100), ConsistencyLevel.STRONG)
        )
        return tuple(_deserialize(record.data) for record in page.items)

    def transact(self, request: AgentTransactionRequest) -> AgentTransactionResult:
        current = self.get_snapshot(request.agent_id, request.user_id, request.workspace_id)
        expected = () if current is None else (self._expected(request.agent_id, current.configuration.config_version),)
        snapshot = AgentSnapshot(request.resulting_agent, request.resulting_configuration)
        change = RecordChange(RecordReference(f"agent:{request.agent_id}"), _RECORD_TYPE, current.configuration.config_version if current else None, _serialize(snapshot, request.event), DataClassification.INTERNAL)
        result = self.persistence.transact(
            TransactionRequest(
                transaction_id=request.transaction_id,
                context=_context(request.user_id, request.workspace_id),
                options=TransactionOptions(), idempotency_key=request.idempotency_key, fingerprint=request.fingerprint,
                expected_versions=expected, changes=(change,), audit=(), outbox=(),
            )
        )
        if not hasattr(result, "receipt"):
            raise AgentIdempotencyConflict("agent transaction rejected")
        receipt = AgentTransactionReceipt(request.transaction_id, result.receipt.commit_state, request.agent_id, request.event.event_id, request.resulting_configuration.config_version)
        if self.event_recorder is not None:
            self.event_recorder.record_event(request.event)
        return AgentTransactionResult(receipt, snapshot, getattr(result, "already_applied", False))

    @staticmethod
    def _expected(agent_id, version):
        from agentos.persistence import ExpectedVersion
        return ExpectedVersion(RecordReference(f"agent:{agent_id}"), version)

    def inspect_commit(self, *, user_id, transaction_id, idempotency_key):
        from agentos.persistence import InspectCommit
        return self.persistence.inspect_commit(InspectCommit(_context(user_id, None), transaction_id, idempotency_key, DataClassification.RESTRICTED))

    def confirmed_outbox(self):
        return ()


class DurableAgentRegistry:
    def __init__(self, *, persistence: DurableAgentTransactionalPersistence, policy=None) -> None:
        self.persistence = persistence
        self.policy = policy
        self._cursors: dict[str, tuple[str, int]] = {}

    def get(self, agent_id, actor: AgentAccessContext):
        snapshot = self.persistence.get_snapshot(agent_id, actor.user_id, actor.workspace_id)
        if snapshot is None or snapshot.agent.owner != actor.actor:
            raise AgentNotFound("agent not found")
        return snapshot

    def list(self, query: AuthorizedAgentQuery):
        snapshots = tuple(item for item in self.persistence.scan(query.user_id, query.workspace_id) if item.agent.owner == query.actor and classification_allows(query.classification.value, item.configuration.prompt.instruction_classification.value))
        offset = 0
        if query.cursor is not None:
            state = self._cursors.get(str(query.cursor))
            if state is None or state[0] != f"{query.user_id}|{query.workspace_id}|{query.actor}":
                raise ValueError("invalid agent cursor")
            offset = state[1]
        page = snapshots[offset : offset + query.limit]
        cursor = None
        if offset + query.limit < len(snapshots):
            token = f"agent-cursor:{len(self._cursors) + 1}"
            self._cursors[token] = (f"{query.user_id}|{query.workspace_id}|{query.actor}", offset + query.limit)
            cursor = AgentPageCursor(token)
        return AgentPage(page, cursor)

    def resolve_for_execution(self, request: AgentResolutionRequest):
        snapshot = self.persistence.get_snapshot(request.agent_id, request.user_id, request.workspace_id)
        if snapshot is None or snapshot.agent.owner != request.actor or snapshot.agent.administrative_state is not AgentAdministrativeState.ACTIVE:
            raise AgentResolutionRejected("agent resolution rejected")
        if self.policy is not None:
            policies = self.policy.validate(snapshot, request)
        else:
            policies = ResolvedAgentPolicies(snapshot.configuration.execution_policy_ref, snapshot.configuration.context_policy_ref, snapshot.configuration.memory_policy_ref, 1, request.purpose, request.classification)
        configuration = snapshot.configuration
        return ResolvedAgent(snapshot.agent.agent_id, snapshot.agent.user_id, request.workspace_id, snapshot.agent.owner, configuration.config_version, configuration.model_profile_ref, configuration.prompt, configuration.presentation, configuration.tool_grants, configuration.capability_grants, configuration.skill_grants, snapshot.agent.private_memory_scope, policies)


class DurableAgentAdministration(AgentAdministration):
    def __init__(self, execution_requester) -> None:
        self.execution_requester = execution_requester

    def __getattr__(self, name):
        if name.startswith("request_"):
            return lambda command: self.execution_requester.request(command)
        raise AttributeError(name)

    def request_create(self, command):
        return self.execution_requester.request(command)


PostgresAgentTransactionalPersistence = DurableAgentTransactionalPersistence
PostgresAgentRegistry = DurableAgentRegistry
PostgresAgentAdministration = DurableAgentAdministration

__all__ = [
    "DurableAgentAdministration", "DurableAgentRegistry", "DurableAgentTransactionalPersistence",
    "PostgresAgentAdministration", "PostgresAgentRegistry", "PostgresAgentTransactionalPersistence",
]
