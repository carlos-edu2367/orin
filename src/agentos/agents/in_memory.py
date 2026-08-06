from __future__ import annotations

import hashlib
import json
from dataclasses import fields, replace
from datetime import UTC, datetime
from typing import Callable

from agentos.events.models import CommitState, DataClassification, EventEnvelope

from .models import (
    Agent,
    AgentAdministrativeState,
    AgentConfiguration,
    AgentId,
    AgentSnapshot,
    AgentConfigVersion,
    CorrelationId,
    MemoryScopeReference,
    OpaqueAgentReference,
    ResolvedAgent,
    ResolvedAgentPolicies,
    WorkspaceAssignment,
)
from .ports import (
    AdministrativeExecutionRef,
    AdministrativeExecutionRequester,
    AdministrativeExecutionState,
    AdministrativeExecutionStatus,
    AgentAccessContext,
    AgentAdministration,
    AgentCommand,
    AgentCommandRejected,
    AgentGrantPolicy,
    AgentIdempotencyConflict,
    AgentNotFound,
    AgentPage,
    AgentPageCursor,
    AgentRegistry,
    AgentResolutionRejected,
    AgentResolutionRequest,
    AgentTransactionReceipt,
    AgentTransactionRequest,
    AgentTransactionResult,
    AgentTransactionalPersistence,
    AgentVersionConflict,
    ArchiveAgent,
    AssignAgentWorkspace,
    AuthorizedAgentQuery,
    CreateAgent,
    ReconfigureAgent,
    ResumeAgent,
    SuspendAgent,
    UnassignAgentWorkspace,
)
from .security import classification_allows, require_same_scope, require_text


def _canonical(value: object) -> object:
    if isinstance(value, OpaqueAgentReference):
        return {"opaque_ref": value.value}
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
        return getattr(value, "value")
    if hasattr(value, "__dataclass_fields__"):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_canonical(item) for item in value]
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    return value


def command_fingerprint(command: AgentCommand) -> str:
    encoded = json.dumps(_canonical(command), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


class AllowAllAgentGrantPolicy(AgentGrantPolicy):
    def validate(self, snapshot: AgentSnapshot, request: AgentResolutionRequest) -> ResolvedAgentPolicies:
        if not classification_allows(request.classification.value, snapshot.configuration.prompt.instruction_classification.value):
            raise AgentResolutionRejected("classification policy rejected")
        return ResolvedAgentPolicies(
            execution_policy_ref=snapshot.configuration.execution_policy_ref,
            context_policy_ref=snapshot.configuration.context_policy_ref,
            memory_policy_ref=snapshot.configuration.memory_policy_ref,
            policy_version=1,
            purpose=request.purpose,
            classification=request.classification,
        )


class InMemoryAgentGrantPolicy(AllowAllAgentGrantPolicy):
    def __init__(self) -> None:
        self.revoked_refs: set[str] = set()
        self.denied_purposes: set[str] = set()

    def revoke(self, reference: OpaqueAgentReference) -> None:
        self.revoked_refs.add(reference.value)

    def deny_purpose(self, purpose: str) -> None:
        self.denied_purposes.add(purpose)

    def validate(self, snapshot: AgentSnapshot, request: AgentResolutionRequest) -> ResolvedAgentPolicies:
        if request.purpose in self.denied_purposes:
            raise AgentResolutionRejected("purpose policy rejected")
        grants = (
            *snapshot.configuration.tool_grants,
            *snapshot.configuration.capability_grants,
            *snapshot.configuration.skill_grants,
        )
        if any(reference.value in self.revoked_refs for reference in grants):
            raise AgentResolutionRejected("grant policy rejected")
        return super().validate(snapshot, request)


class InMemoryAgentTransactionalPersistence(AgentTransactionalPersistence):
    """Replaceable in-memory adapter; it is the only durable authority in tests."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._configurations: dict[tuple[str, int], AgentConfiguration] = {}
        self._idempotency: dict[tuple[str, str | None, str], tuple[str, AgentTransactionResult]] = {}
        self._receipts: dict[tuple[str, str], AgentTransactionReceipt] = {}
        self._outbox: list[EventEnvelope] = []
        self._event_states: dict[str, CommitState] = {}
        self.audit_log: list[object] = []
        self._reject_next = False
        self._not_committed_next = False
        self._unknown_next = False

    def get_snapshot(self, agent_id, user_id, workspace_id, config_version=None):
        agent = self._agents.get(str(agent_id))
        if agent is None or str(agent.user_id) != str(user_id) or agent.workspace_id != workspace_id:
            return None
        version = int(config_version or agent.current_config_version)
        configuration = self._configurations.get((str(agent_id), version))
        if configuration is None:
            return None
        return AgentSnapshot(agent=agent, configuration=configuration)

    def scan(self, user_id, workspace_id):
        result = []
        for agent in sorted(self._agents.values(), key=lambda item: str(item.agent_id)):
            snapshot = self.get_snapshot(agent.agent_id, user_id, workspace_id)
            if snapshot is not None:
                result.append(snapshot)
        return tuple(result)

    def transact(self, request: AgentTransactionRequest) -> AgentTransactionResult:
        key = (str(request.user_id), str(request.workspace_id) if request.workspace_id is not None else None, request.idempotency_key)
        existing = self._idempotency.get(key)
        if existing is not None:
            previous_fingerprint, previous_result = existing
            if previous_fingerprint != request.fingerprint:
                raise AgentIdempotencyConflict("idempotency key conflict")
            return replace(previous_result, already_applied=True)
        if self._reject_next:
            self._reject_next = False
            raise AgentCommandRejected("agent transaction rejected")
        if self._not_committed_next:
            self._not_committed_next = False
            raise AgentCommandRejected("agent transaction not committed")
        current = self._agents.get(str(request.agent_id))
        if current is not None and current.user_id != request.user_id:
            raise AgentCommandRejected("agent transaction rejected")
        self._agents[str(request.agent_id)] = request.resulting_agent
        self._configurations[(str(request.agent_id), int(request.resulting_configuration.config_version))] = request.resulting_configuration
        receipt = AgentTransactionReceipt(
            transaction_id=request.transaction_id,
            commit_state=CommitState.COMMITTED,
            agent_id=request.agent_id,
            event_id=request.event.event_id,
            config_version=request.resulting_configuration.config_version,
        )
        result = AgentTransactionResult(
            receipt=receipt,
            snapshot=AgentSnapshot(request.resulting_agent, request.resulting_configuration),
        )
        self._idempotency[key] = (request.fingerprint, result)
        self._receipts[(str(request.user_id), request.transaction_id)] = receipt
        self.audit_log.append(request.event.event_id)
        self._outbox.append(request.event)
        self._event_states[request.event.event_id] = CommitState.COMMITTED
        if self._unknown_next:
            self._unknown_next = False
            unknown_receipt = replace(receipt, commit_state=CommitState.UNKNOWN)
            unknown_result = replace(result, receipt=unknown_receipt)
            self._idempotency[key] = (request.fingerprint, unknown_result)
            self._receipts[(str(request.user_id), request.transaction_id)] = unknown_receipt
            self._event_states[request.event.event_id] = CommitState.UNKNOWN
            return unknown_result
        return result

    def inspect_commit(self, *, user_id, transaction_id, idempotency_key):
        receipt = self._receipts.get((str(user_id), transaction_id))
        if receipt is None:
            raise LookupError("transaction not found")
        return receipt

    def confirmed_outbox(self):
        return tuple(self._outbox)

    def outbox_records(self):
        from .compat import agent_outbox_records

        return agent_outbox_records(self)

    def reject_next(self) -> None:
        self._reject_next = True

    def not_committed_next(self) -> None:
        self._not_committed_next = True

    def indeterminate_next(self) -> None:
        self._unknown_next = True


class InMemoryAdministrativeExecutionRequester(AdministrativeExecutionRequester):
    def __init__(self) -> None:
        self._counter = 0
        self._pending: dict[str, tuple[AgentCommand, str, AdministrativeExecutionStatus, AgentTransactionResult | None]] = {}
        self._by_key: dict[tuple[str, str | None, str], str] = {}
        self._administration: InMemoryAgentAdministration | None = None

    def bind(self, administration: "InMemoryAgentAdministration") -> None:
        self._administration = administration

    def request(self, command: AgentCommand) -> AdministrativeExecutionRef:
        key = (str(command.user_id), str(command.workspace_id) if command.workspace_id is not None else None, command.idempotency_key)
        fingerprint = command_fingerprint(command)
        existing_id = self._by_key.get(key)
        if existing_id is not None:
            existing_command, existing_fingerprint, _, _ = self._pending[existing_id]
            if existing_fingerprint != fingerprint:
                raise AgentIdempotencyConflict("idempotency key conflict")
            return AdministrativeExecutionRef(existing_id, existing_command.correlation_id, existing_command.idempotency_key)
        self._counter += 1
        execution_id = f"agent-admin:{self._counter}"
        self._pending[execution_id] = (command, fingerprint, AdministrativeExecutionStatus.REQUESTED, None)
        self._by_key[key] = execution_id
        return AdministrativeExecutionRef(execution_id, command.correlation_id, command.idempotency_key)

    def confirm(self, reference: AdministrativeExecutionRef):
        if self._administration is None:
            raise RuntimeError("administrative execution requester is not bound")
        pending = self._pending.get(reference.execution_id)
        if pending is None:
            raise LookupError("administrative execution not found")
        command, fingerprint, status, result = pending
        if status is AdministrativeExecutionStatus.CONFIRMED:
            return result
        if status is AdministrativeExecutionStatus.CANCELLED:
            return None
        if status is AdministrativeExecutionStatus.UNKNOWN:
            assert result is not None
            receipt = self._administration.persistence.inspect_commit(
                user_id=command.user_id,
                transaction_id=reference.execution_id,
                idempotency_key=command.idempotency_key,
            )
            self._pending[reference.execution_id] = (command, fingerprint, AdministrativeExecutionStatus.CONFIRMED, replace(result, receipt=receipt))
            return replace(result, receipt=receipt)
        try:
            result = self._administration._confirm(reference, command, fingerprint)
        except AgentCommandRejected:
            self._pending[reference.execution_id] = (command, fingerprint, AdministrativeExecutionStatus.NOT_COMMITTED, None)
            raise
        if result.receipt.commit_state is CommitState.UNKNOWN:
            status = AdministrativeExecutionStatus.UNKNOWN
        else:
            status = AdministrativeExecutionStatus.CONFIRMED
        self._pending[reference.execution_id] = (command, fingerprint, status, result)
        return result

    def cancel(self, reference: AdministrativeExecutionRef) -> AdministrativeExecutionStatus:
        pending = self._pending.get(reference.execution_id)
        if pending is None:
            raise LookupError("administrative execution not found")
        command, fingerprint, status, result = pending
        if status is AdministrativeExecutionStatus.REQUESTED:
            status = AdministrativeExecutionStatus.CANCELLED
            self._pending[reference.execution_id] = (command, fingerprint, status, result)
        return status

    def inspect(self, reference: AdministrativeExecutionRef) -> AdministrativeExecutionState:
        pending = self._pending.get(reference.execution_id)
        if pending is None:
            raise LookupError("administrative execution not found")
        _, _, status, result = pending
        return AdministrativeExecutionState(reference, status, result)

    def command(self, reference: AdministrativeExecutionRef) -> AgentCommand:
        pending = self._pending.get(reference.execution_id)
        if pending is None:
            raise LookupError("administrative execution not found")
        return pending[0]


class InMemoryAgentAdministration(AgentAdministration):
    def __init__(self, *, persistence, execution_requester, now: Callable[[], datetime] = _now) -> None:
        self.persistence: InMemoryAgentTransactionalPersistence = persistence
        self.execution_requester: InMemoryAdministrativeExecutionRequester = execution_requester
        self.now = now

    def request_create(self, command: CreateAgent) -> AdministrativeExecutionRef:
        return self.execution_requester.request(command)

    def request_reconfigure(self, command: ReconfigureAgent) -> AdministrativeExecutionRef:
        return self.execution_requester.request(command)

    def request_suspend(self, command: SuspendAgent) -> AdministrativeExecutionRef:
        return self.execution_requester.request(command)

    def request_resume(self, command: ResumeAgent) -> AdministrativeExecutionRef:
        return self.execution_requester.request(command)

    def request_archive(self, command: ArchiveAgent) -> AdministrativeExecutionRef:
        return self.execution_requester.request(command)

    def request_assign_workspace(self, command: AssignAgentWorkspace) -> AdministrativeExecutionRef:
        return self.execution_requester.request(command)

    def request_unassign_workspace(self, command: UnassignAgentWorkspace) -> AdministrativeExecutionRef:
        return self.execution_requester.request(command)

    def suspend_command(self, **values) -> SuspendAgent:
        return SuspendAgent(
            correlation_id=values.pop("correlation_id", "correlation:suspend"),
            idempotency_key=values.pop("idempotency_key", "suspend:1"),
            requested_at=values.pop("requested_at", self.now()),
            **values,
        )

    def resume_command(self, **values) -> ResumeAgent:
        return ResumeAgent(
            correlation_id=values.pop("correlation_id", "correlation:resume"),
            idempotency_key=values.pop("idempotency_key", "resume:1"),
            requested_at=values.pop("requested_at", self.now()),
            **values,
        )

    def archive_command(self, **values) -> ArchiveAgent:
        return ArchiveAgent(
            correlation_id=values.pop("correlation_id", "correlation:archive"),
            idempotency_key=values.pop("idempotency_key", "archive:1"),
            requested_at=values.pop("requested_at", self.now()),
            **values,
        )

    def _confirm(self, reference, command: AgentCommand, fingerprint: str) -> AgentTransactionResult:
        current = self.persistence.get_snapshot(command.agent_id, command.user_id, command.workspace_id)
        configuration: AgentConfiguration
        if isinstance(command, CreateAgent):
            if current is not None:
                raise AgentCommandRejected("agent already exists")
            if command.owner != command.actor:
                raise AgentCommandRejected("agent ownership rejected")
            agent = Agent(
                agent_id=command.agent_id,
                user_id=command.user_id,
                workspace_id=command.workspace_id,
                owner=command.owner,
                display_name=command.display_name,
                administrative_state=AgentAdministrativeState.ACTIVE,
                current_config_version=1,
                private_memory_scope=command.private_memory_scope,
                created_by=command.actor,
                created_at=command.requested_at,
                updated_at=command.requested_at,
                suspended_at=None,
                archived_at=None,
                audit_refs=(OpaqueAgentReference(f"audit:{reference.execution_id}"),),
            )
            configuration = command.initial_configuration
            event_type = "AgentCreated"
            operation = "CREATE"
        else:
            if current is None:
                raise AgentCommandRejected("agent operation rejected")
            if current.agent.owner != command.actor:
                raise AgentCommandRejected("agent ownership rejected")
            if current.agent.administrative_state is AgentAdministrativeState.ARCHIVED:
                raise AgentCommandRejected("archived Agent is read-only")
            if command.expected_version is not None and current.configuration.config_version != command.expected_version:
                raise AgentVersionConflict("agent version conflict")
            agent = current.agent
            configuration = current.configuration
            operation = type(command).__name__.replace("Agent", "").upper()
            event_type = {
                ReconfigureAgent: "AgentConfigurationChanged",
                SuspendAgent: "AgentSuspended",
                ResumeAgent: "AgentResumed",
                ArchiveAgent: "AgentArchived",
                AssignAgentWorkspace: "AgentWorkspaceAssigned",
                UnassignAgentWorkspace: "AgentWorkspaceUnassigned",
            }[type(command)]
            if isinstance(command, ReconfigureAgent):
                configuration = command.configuration
                agent = replace(agent, current_config_version=configuration.config_version, updated_at=command.requested_at)
            elif isinstance(command, (SuspendAgent, ResumeAgent, ArchiveAgent)):
                try:
                    agent = agent.transition_to(command.target_state, now=command.requested_at)
                except ValueError as exc:
                    raise AgentCommandRejected("agent state transition rejected") from exc
            elif isinstance(command, AssignAgentWorkspace):
                assignment = WorkspaceAssignment(
                    workspace_id=command.assigned_workspace_id,
                    assignment_ref=command.assignment_ref,
                    assigned_by=command.actor,
                    assigned_at=command.requested_at,
                )
                assignments = tuple(item for item in configuration.workspace_assignments if item.workspace_id != assignment.workspace_id) + (assignment,)
                configuration = replace(
                    configuration,
                    config_version=configuration.config_version + 1,
                    supersedes_version=configuration.config_version,
                    workspace_assignments=assignments,
                )
                agent = replace(agent, current_config_version=configuration.config_version, updated_at=command.requested_at)
            elif isinstance(command, UnassignAgentWorkspace):
                assignments = tuple(item for item in configuration.workspace_assignments if item.workspace_id != command.assigned_workspace_id)
                configuration = replace(
                    configuration,
                    config_version=configuration.config_version + 1,
                    supersedes_version=configuration.config_version,
                    workspace_assignments=assignments,
                )
                agent = replace(agent, current_config_version=configuration.config_version, updated_at=command.requested_at)
        payload = {
            "agent_id": str(command.agent_id),
            "agent_version": int(configuration.config_version),
            "operation_code": operation,
        }
        if isinstance(command, (SuspendAgent, ResumeAgent, ArchiveAgent)):
            payload["state_code"] = agent.administrative_state.value
        if isinstance(command, (AssignAgentWorkspace, UnassignAgentWorkspace)):
            payload["workspace_code"] = str(command.assigned_workspace_id)
        event = EventEnvelope(
            event_id=f"event:{reference.execution_id}:1",
            event_type=event_type,
            event_version=1,
            occurred_at=command.requested_at,
            source="agents",
            correlation_id=str(command.correlation_id),
            causation_id=command.causation_id or command.idempotency_key,
            sequence=1,
            user_id=str(command.user_id),
            workspace_id=(
                str(command.assigned_workspace_id)
                if isinstance(command, (AssignAgentWorkspace, UnassignAgentWorkspace))
                else str(command.workspace_id) if command.workspace_id is not None else None
            ),
            agent_id=str(command.agent_id),
            execution_id=reference.execution_id,
            classification=DataClassification.INTERNAL,
            payload=payload,
        )
        request = AgentTransactionRequest(
            transaction_id=reference.execution_id,
            idempotency_key=command.idempotency_key,
            fingerprint=fingerprint,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            agent_id=command.agent_id,
            resulting_agent=agent,
            resulting_configuration=configuration,
            event=event,
        )
        return self.persistence.transact(request)


class InMemoryAgentRegistry(AgentRegistry):
    def __init__(self, *, persistence, policy: AgentGrantPolicy | None = None) -> None:
        self.persistence: InMemoryAgentTransactionalPersistence = persistence
        self.policy = policy or AllowAllAgentGrantPolicy()

    def get(self, agent_id: AgentId, actor: AgentAccessContext):
        snapshot = self.persistence.get_snapshot(agent_id, actor.user_id, actor.workspace_id)
        if snapshot is None or snapshot.agent.owner != actor.actor:
            raise AgentNotFound("agent not found")
        return snapshot

    def list(self, query: AuthorizedAgentQuery) -> AgentPage:
        snapshots = tuple(
            snapshot
            for snapshot in self.persistence.scan(query.user_id, query.workspace_id)
            if snapshot.agent.owner == query.actor
            if classification_allows(query.classification.value, snapshot.configuration.prompt.instruction_classification.value)
        )
        offset = 0
        if query.cursor is not None:
            try:
                offset = int(query.cursor.value.rsplit(":", 1)[1])
            except (ValueError, IndexError) as exc:
                raise ValueError("invalid agent cursor") from exc
        page = snapshots[offset : offset + query.limit]
        next_cursor = AgentPageCursor(f"agent-cursor:{offset + query.limit}") if offset + query.limit < len(snapshots) else None
        return AgentPage(page, next_cursor)

    def resolve_for_execution(self, request: AgentResolutionRequest) -> ResolvedAgent:
        snapshot = self.persistence.get_snapshot(request.agent_id, request.user_id, request.workspace_id)
        if snapshot is None:
            # A user-scoped Agent can be used in an assigned Workspace, so retry the identity lookup only internally.
            candidate = self.persistence._agents.get(str(request.agent_id))
            if candidate is None or candidate.user_id != request.user_id:
                raise AgentResolutionRejected("agent resolution rejected")
            snapshot = self.persistence.get_snapshot(request.agent_id, request.user_id, candidate.workspace_id)
            if snapshot is None:
                raise AgentResolutionRejected("agent resolution rejected")
        if request.actor is not None and snapshot.agent.owner != request.actor:
            raise AgentResolutionRejected("agent ownership rejected")
        if snapshot.agent.administrative_state is not AgentAdministrativeState.ACTIVE:
            raise AgentResolutionRejected("agent is not active")
        if snapshot.agent.workspace_id is not None:
            if snapshot.agent.workspace_id != request.workspace_id:
                raise AgentResolutionRejected("workspace assignment rejected")
        elif request.workspace_id is not None:
            if not any(item.workspace_id == request.workspace_id for item in snapshot.configuration.workspace_assignments):
                raise AgentResolutionRejected("workspace assignment rejected")
        configuration = snapshot.configuration
        if request.requested_config_version is not None:
            explicit = self.persistence.get_snapshot(
                request.agent_id, request.user_id, snapshot.agent.workspace_id, request.requested_config_version
            )
            if explicit is None:
                raise AgentResolutionRejected("configuration version rejected")
            configuration = explicit.configuration
            snapshot = AgentSnapshot(snapshot.agent, configuration)
        policies = self.policy.validate(snapshot, request)
        return ResolvedAgent(
            agent_id=snapshot.agent.agent_id,
            user_id=snapshot.agent.user_id,
            workspace_id=request.workspace_id,
            owner=snapshot.agent.owner,
            config_version=configuration.config_version,
            model_profile_ref=configuration.model_profile_ref,
            prompt=configuration.prompt,
            presentation=configuration.presentation,
            tool_grants=configuration.tool_grants,
            capability_grants=configuration.capability_grants,
            skill_grants=configuration.skill_grants,
            private_memory_scope=snapshot.agent.private_memory_scope,
            policies=policies,
        )


__all__ = [
    "AllowAllAgentGrantPolicy",
    "InMemoryAgentAdministration",
    "InMemoryAgentGrantPolicy",
    "InMemoryAgentRegistry",
    "InMemoryAgentTransactionalPersistence",
    "InMemoryAdministrativeExecutionRequester",
    "command_fingerprint",
]
