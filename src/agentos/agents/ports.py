from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from agentos.events.models import CommitState, DataClassification, EventEnvelope

from .models import (
    Agent,
    AgentConfigVersion,
    AgentConfiguration,
    AgentId,
    AgentSnapshot,
    AgentAdministrativeState,
    CorrelationId,
    MemoryScopeReference,
    OpaqueAgentReference,
    ResolvedAgent,
    ResolvedAgentPolicies,
    UserId,
    Version,
    WorkspaceAssignment,
    WorkspaceId,
)
from .security import require_aware, require_text


class AgentError(ValueError):
    """Base class for sanitized public Agent failures."""


class AgentNotFound(LookupError, AgentError):
    pass


class AgentAccessDenied(PermissionError, AgentError):
    pass


class AgentResolutionRejected(AgentError):
    pass


class AgentIdempotencyConflict(AgentError):
    pass


class AgentVersionConflict(AgentError):
    pass


class AgentCommandRejected(AgentError):
    pass


class AgentTransactionUnknown(AgentError):
    pass


@dataclass(frozen=True, slots=True)
class AgentAccessContext:
    user_id: UserId
    workspace_id: WorkspaceId | None
    actor: str
    purpose: str = "agent.read"

    def __post_init__(self) -> None:
        require_text(self.user_id, "user_id")
        if self.workspace_id is not None:
            require_text(self.workspace_id, "workspace_id")
        require_text(self.actor, "actor")
        require_text(self.purpose, "purpose", maximum=128)


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentCommand:
    actor: str
    user_id: UserId
    workspace_id: WorkspaceId | None
    agent_id: AgentId
    correlation_id: CorrelationId
    idempotency_key: str
    requested_at: datetime
    expected_version: Version | None = None
    causation_id: str | None = None

    def __post_init__(self) -> None:
        for field, value in (
            ("actor", self.actor),
            ("user_id", self.user_id),
            ("agent_id", self.agent_id),
            ("correlation_id", self.correlation_id),
            ("idempotency_key", self.idempotency_key),
        ):
            require_text(value, field)
        if self.workspace_id is not None:
            require_text(self.workspace_id, "workspace_id")
        require_aware(self.requested_at, "requested_at")
        if self.expected_version is not None and self.expected_version < 1:
            raise ValueError("expected_version must be positive")
        if self.causation_id is not None:
            require_text(self.causation_id, "causation_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateAgent(AgentCommand):
    owner: str
    display_name: str
    initial_configuration: AgentConfiguration
    private_memory_scope: MemoryScopeReference

    def __post_init__(self) -> None:
        AgentCommand.__post_init__(self)
        require_text(self.owner, "owner")
        require_text(self.display_name, "display_name", maximum=128)
        if self.initial_configuration.agent_id != self.agent_id:
            raise ValueError("initial configuration Agent does not match command")
        if self.initial_configuration.config_version != 1:
            raise ValueError("initial configuration version must be one")
        if self.private_memory_scope.agent_id != self.agent_id or self.private_memory_scope.user_id != self.user_id:
            raise ValueError("private memory scope does not match command ownership")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconfigureAgent(AgentCommand):
    configuration: AgentConfiguration

    def __post_init__(self) -> None:
        AgentCommand.__post_init__(self)
        if self.configuration.agent_id != self.agent_id:
            raise ValueError("configuration Agent does not match command")
        if self.expected_version is None:
            raise ValueError("reconfiguration requires expected_version")


@dataclass(frozen=True, slots=True, kw_only=True)
class SuspendAgent(AgentCommand):
    @property
    def target_state(self) -> AgentAdministrativeState:
        return AgentAdministrativeState.SUSPENDED


@dataclass(frozen=True, slots=True, kw_only=True)
class ResumeAgent(AgentCommand):
    @property
    def target_state(self) -> AgentAdministrativeState:
        return AgentAdministrativeState.ACTIVE


@dataclass(frozen=True, slots=True, kw_only=True)
class ArchiveAgent(AgentCommand):
    @property
    def target_state(self) -> AgentAdministrativeState:
        return AgentAdministrativeState.ARCHIVED


@dataclass(frozen=True, slots=True, kw_only=True)
class AssignAgentWorkspace(AgentCommand):
    assigned_workspace_id: WorkspaceId
    assignment_ref: OpaqueAgentReference

    def __post_init__(self) -> None:
        AgentCommand.__post_init__(self)
        require_text(self.assigned_workspace_id, "assigned_workspace_id")
        if not isinstance(self.assignment_ref, OpaqueAgentReference):
            raise ValueError("assignment_ref must be opaque")


@dataclass(frozen=True, slots=True, kw_only=True)
class UnassignAgentWorkspace(AgentCommand):
    assigned_workspace_id: WorkspaceId

    def __post_init__(self) -> None:
        AgentCommand.__post_init__(self)
        require_text(self.assigned_workspace_id, "assigned_workspace_id")


@dataclass(frozen=True, slots=True)
class AgentResolutionRequest:
    agent_id: AgentId
    user_id: UserId
    workspace_id: WorkspaceId | None
    requested_config_version: AgentConfigVersion | None
    purpose: str
    correlation_id: CorrelationId
    classification: DataClassification = DataClassification.INTERNAL
    actor: str | None = None

    def __post_init__(self) -> None:
        require_text(self.agent_id, "agent_id")
        require_text(self.user_id, "user_id")
        if self.workspace_id is not None:
            require_text(self.workspace_id, "workspace_id")
        if self.requested_config_version is not None and self.requested_config_version < 1:
            raise ValueError("requested_config_version must be positive")
        require_text(self.purpose, "purpose", maximum=128)
        require_text(self.correlation_id, "correlation_id")
        if self.actor is not None:
            require_text(self.actor, "actor")
        object.__setattr__(self, "classification", DataClassification(self.classification))


@dataclass(frozen=True, slots=True)
class AgentPageCursor:
    value: str

    def __post_init__(self) -> None:
        require_text(self.value, "cursor", maximum=128)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class AuthorizedAgentQuery:
    user_id: UserId
    workspace_id: WorkspaceId | None
    actor: str
    purpose: str
    limit: int = 50
    cursor: AgentPageCursor | None = None
    classification: DataClassification = DataClassification.INTERNAL

    def __post_init__(self) -> None:
        require_text(self.user_id, "user_id")
        if self.workspace_id is not None:
            require_text(self.workspace_id, "workspace_id")
        require_text(self.actor, "actor")
        require_text(self.purpose, "purpose", maximum=128)
        if self.limit < 1 or self.limit > 100:
            raise ValueError("limit must be between one and one hundred")
        object.__setattr__(self, "classification", DataClassification(self.classification))


@dataclass(frozen=True, slots=True)
class AgentPage:
    items: tuple[AgentSnapshot, ...]
    next_cursor: AgentPageCursor | None


@dataclass(frozen=True, slots=True)
class AdministrativeExecutionRef:
    execution_id: str
    correlation_id: CorrelationId
    idempotency_key: str

    def __post_init__(self) -> None:
        require_text(self.execution_id, "execution_id")
        require_text(self.correlation_id, "correlation_id")
        require_text(self.idempotency_key, "idempotency_key")


class AdministrativeExecutionStatus(StrEnum):
    REQUESTED = "REQUESTED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    NOT_COMMITTED = "NOT_COMMITTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class AdministrativeExecutionState:
    reference: AdministrativeExecutionRef
    status: AdministrativeExecutionStatus
    result: "AgentTransactionResult | None" = None


@dataclass(frozen=True, slots=True)
class AgentTransactionReceipt:
    transaction_id: str
    commit_state: CommitState
    agent_id: AgentId
    event_id: str
    config_version: AgentConfigVersion


@dataclass(frozen=True, slots=True)
class AgentTransactionRequest:
    transaction_id: str
    idempotency_key: str
    fingerprint: str
    user_id: UserId
    workspace_id: WorkspaceId | None
    agent_id: AgentId
    resulting_agent: Agent
    resulting_configuration: AgentConfiguration
    event: EventEnvelope


@dataclass(frozen=True, slots=True)
class AgentTransactionResult:
    receipt: AgentTransactionReceipt
    snapshot: AgentSnapshot
    already_applied: bool = False


@dataclass(frozen=True, slots=True)
class AgentAuditRecord:
    audit_ref: OpaqueAgentReference
    agent_id: AgentId
    user_id: UserId
    workspace_id: WorkspaceId | None
    execution_id: str
    correlation_id: CorrelationId
    operation: str
    resulting_version: AgentConfigVersion


class AgentGrantPolicy(Protocol):
    def validate(
        self, snapshot: AgentSnapshot, request: AgentResolutionRequest
    ) -> ResolvedAgentPolicies: ...


class AgentRegistry(Protocol):
    def get(self, agent_id: AgentId, actor: AgentAccessContext) -> AgentSnapshot: ...

    def resolve_for_execution(self, request: AgentResolutionRequest) -> ResolvedAgent: ...

    def list(self, query: AuthorizedAgentQuery) -> AgentPage: ...


class AgentTransactionalPersistence(Protocol):
    def get_snapshot(
        self,
        agent_id: AgentId,
        user_id: UserId,
        workspace_id: WorkspaceId | None,
        config_version: AgentConfigVersion | None = None,
    ) -> AgentSnapshot | None: ...

    def scan(self, user_id: UserId, workspace_id: WorkspaceId | None) -> tuple[AgentSnapshot, ...]: ...

    def transact(self, request: AgentTransactionRequest) -> AgentTransactionResult: ...

    def inspect_commit(
        self, *, user_id: UserId, transaction_id: str, idempotency_key: str
    ) -> AgentTransactionReceipt: ...

    def confirmed_outbox(self) -> tuple[EventEnvelope, ...]: ...


class AdministrativeExecutionRequester(Protocol):
    def request(self, command: AgentCommand) -> AdministrativeExecutionRef: ...

    def confirm(self, reference: AdministrativeExecutionRef) -> AgentTransactionResult | None: ...

    def cancel(self, reference: AdministrativeExecutionRef) -> AdministrativeExecutionStatus: ...

    def inspect(self, reference: AdministrativeExecutionRef) -> AdministrativeExecutionState: ...


class AgentAdministration(Protocol):
    def request_create(self, command: CreateAgent) -> AdministrativeExecutionRef: ...

    def request_reconfigure(self, command: ReconfigureAgent) -> AdministrativeExecutionRef: ...

    def request_suspend(self, command: SuspendAgent) -> AdministrativeExecutionRef: ...

    def request_resume(self, command: ResumeAgent) -> AdministrativeExecutionRef: ...

    def request_archive(self, command: ArchiveAgent) -> AdministrativeExecutionRef: ...

    def request_assign_workspace(self, command: AssignAgentWorkspace) -> AdministrativeExecutionRef: ...

    def request_unassign_workspace(self, command: UnassignAgentWorkspace) -> AdministrativeExecutionRef: ...


__all__ = [
    "AdministrativeExecutionRef",
    "AdministrativeExecutionRequester",
    "AdministrativeExecutionState",
    "AdministrativeExecutionStatus",
    "AgentAccessContext",
    "AgentAccessDenied",
    "AgentAdministration",
    "AgentAuditRecord",
    "AgentCommand",
    "AgentCommandRejected",
    "AgentError",
    "AgentGrantPolicy",
    "AgentIdempotencyConflict",
    "AgentNotFound",
    "AgentPage",
    "AgentPageCursor",
    "AgentRegistry",
    "AgentResolutionRejected",
    "AgentResolutionRequest",
    "AgentTransactionReceipt",
    "AgentTransactionRequest",
    "AgentTransactionResult",
    "AgentTransactionalPersistence",
    "AgentVersionConflict",
    "ArchiveAgent",
    "AssignAgentWorkspace",
    "AuthorizedAgentQuery",
    "CreateAgent",
    "ReconfigureAgent",
    "ResumeAgent",
    "SuspendAgent",
    "UnassignAgentWorkspace",
]
