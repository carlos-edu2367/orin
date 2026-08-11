from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType
import re

from agentos.context.sharing import HandoffRef, StructuredHandoff, TaskSnapshot
from agentos.events import DataClassification
from agentos.execution.models import ExecutionLimits


UserId = NewType("UserId", str)
WorkspaceId = NewType("WorkspaceId", str)
AgentId = NewType("AgentId", str)
CorrelationId = NewType("CorrelationId", str)
ExecutionId = NewType("ExecutionId", str)
IdempotencyKey = NewType("IdempotencyKey", str)


def _required(value: object, field: str, *, maximum: int = 256) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds its bound")


def _opaque_ref(value: object, field: str) -> None:
    _required(value, field, maximum=512)
    text = str(value)
    if re.match(r"^[A-Za-z0-9][A-Za-z0-9:._/-]*$", text) is None or any(token in text.lower() for token in ("password", "secret", "token=", "api_key", "credential")):
        raise ValueError(f"{field} must be an opaque reference")


def _aware(value: datetime | None, field: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field} must be timezone-aware")


def _classification(value: DataClassification) -> DataClassification:
    try:
        return DataClassification(value)
    except ValueError as exc:
        raise ValueError("classification is invalid") from exc


class ParticipantState(StrEnum):
    ACTIVE = "ACTIVE"
    REMOVED = "REMOVED"


class CollaborationState(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class AgentMessageKind(StrEnum):
    INFORM = "INFORM"
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    CONTROL_NOTICE = "CONTROL_NOTICE"


class DelegationFailurePolicy(StrEnum):
    PROPAGATE = "PROPAGATE"
    CONTINUE_WITH_FAILURE_REF = "CONTINUE_WITH_FAILURE_REF"
    REQUEST_RETRY = "REQUEST_RETRY"


class DelegationCancellationPolicy(StrEnum):
    CASCADE = "CASCADE"
    DETACH_IF_AUTHORIZED = "DETACH_IF_AUTHORIZED"
    CANCEL_CHILD_ONLY = "CANCEL_CHILD_ONLY"


class CompletionRule(StrEnum):
    ALL = "ALL"
    ANY = "ANY"
    MINIMUM_COUNT = "MINIMUM_COUNT"


class DelegationTerminalState(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CancellationScope(StrEnum):
    PARENT = "PARENT"
    CHILD = "CHILD"
    SUBTREE = "SUBTREE"


@dataclass(frozen=True, slots=True)
class CollaborationPolicy:
    maximum_participants: int
    allowed_purposes: tuple[str, ...]
    classification_ceiling: DataClassification

    def __post_init__(self) -> None:
        if self.maximum_participants < 1 or self.maximum_participants > 64:
            raise ValueError("maximum_participants must be between 1 and 64")
        object.__setattr__(self, "allowed_purposes", tuple(self.allowed_purposes))
        if not self.allowed_purposes or len(self.allowed_purposes) > 32:
            raise ValueError("allowed_purposes must be bounded and non-empty")
        for purpose in self.allowed_purposes:
            _required(purpose, "purpose", maximum=128)
        object.__setattr__(self, "classification_ceiling", _classification(self.classification_ceiling))


@dataclass(frozen=True, slots=True)
class CollaborationParticipant:
    agent_id: str
    state: ParticipantState
    joined_at: datetime
    removed_at: datetime | None = None

    def __post_init__(self) -> None:
        _required(self.agent_id, "agent_id")
        object.__setattr__(self, "state", ParticipantState(self.state))
        _aware(self.joined_at, "joined_at")
        _aware(self.removed_at, "removed_at")
        if self.state is ParticipantState.REMOVED and self.removed_at is None:
            raise ValueError("removed participant requires removed_at")


@dataclass(frozen=True, slots=True)
class Collaboration:
    collaboration_id: str
    user_id: str
    workspace_id: str | None
    owner: str
    participant_agent_ids: tuple[str, ...]
    coordinator_agent_id: str | None
    policy: CollaborationPolicy
    correlation_id: str
    created_at: datetime
    version: int
    state: CollaborationState = CollaborationState.ACTIVE
    participant_records: tuple[CollaborationParticipant, ...] = ()

    def __post_init__(self) -> None:
        for field in ("collaboration_id", "user_id", "owner", "correlation_id"):
            _required(getattr(self, field), field)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        object.__setattr__(self, "participant_agent_ids", tuple(self.participant_agent_ids))
        if not self.participant_agent_ids or len(self.participant_agent_ids) > self.policy.maximum_participants:
            raise ValueError("participant list exceeds collaboration policy")
        if len(set(self.participant_agent_ids)) != len(self.participant_agent_ids):
            raise ValueError("participant agents must be unique")
        if self.coordinator_agent_id is not None and self.coordinator_agent_id not in self.participant_agent_ids:
            raise ValueError("coordinator must be a participant")
        if self.version < 1:
            raise ValueError("version must be positive")
        object.__setattr__(self, "state", CollaborationState(self.state))
        records = tuple(self.participant_records)
        if not records:
            records = tuple(CollaborationParticipant(agent_id, ParticipantState.ACTIVE, self.created_at) for agent_id in self.participant_agent_ids)
        if {record.agent_id for record in records} != set(self.participant_agent_ids):
            raise ValueError("participant records must match participant_agent_ids")
        object.__setattr__(self, "participant_records", records)
        _aware(self.created_at, "created_at")

    def participant(self, agent_id: str) -> CollaborationParticipant:
        for participant in self.participant_records:
            if participant.agent_id == agent_id:
                return participant
        raise KeyError(agent_id)


@dataclass(frozen=True, slots=True)
class AgentMessage:
    message_id: str
    collaboration_id: str
    sender_agent_id: str
    recipient_agent_id: str
    user_id: str
    workspace_id: str | None
    owner: str
    purpose: str
    classification: DataClassification
    correlation_id: str
    causation_id: str | None
    delivery_execution_id: str
    kind: AgentMessageKind
    inline_summary: str | None
    content_refs: tuple[str, ...]
    handoff_ref: HandoffRef | None
    deadline_at: datetime | None
    idempotency_key: str
    created_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "message_id", "collaboration_id", "sender_agent_id", "recipient_agent_id",
            "user_id", "owner", "purpose", "correlation_id", "delivery_execution_id",
            "idempotency_key",
        ):
            _required(getattr(self, field), field)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        if self.causation_id is not None:
            _required(self.causation_id, "causation_id")
        if self.inline_summary is not None:
            _required(self.inline_summary, "inline_summary", maximum=256)
            if any(token in self.inline_summary.lower() for token in ("secret", "password", "token", "credential")):
                raise ValueError("inline_summary contains protected data")
        object.__setattr__(self, "content_refs", tuple(self.content_refs))
        if len(self.content_refs) > 16:
            raise ValueError("content_refs exceeds its bound")
        for ref in self.content_refs:
            _opaque_ref(ref, "content_ref")
        object.__setattr__(self, "classification", _classification(self.classification))
        object.__setattr__(self, "kind", AgentMessageKind(self.kind))
        _aware(self.deadline_at, "deadline_at")
        _aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class Delegation:
    delegation_id: str
    collaboration_id: str
    parent_execution_id: str
    child_execution_id: str
    delegator_agent_id: str
    delegate_agent_id: str
    handoff_ref: HandoffRef
    user_id: str
    workspace_id: str | None
    owner: str
    purpose: str
    classification: DataClassification
    authorization_ref: str
    correlation_id: str
    causation_id: str
    deadline_at: datetime | None
    failure_policy: DelegationFailurePolicy
    cancellation_policy: DelegationCancellationPolicy
    idempotency_key: str
    created_at: datetime
    attempt: int = 1
    parent_delegation_id: str | None = None

    def __post_init__(self) -> None:
        for field in (
            "delegation_id", "collaboration_id", "parent_execution_id", "child_execution_id",
            "delegator_agent_id", "delegate_agent_id", "user_id", "owner", "purpose",
            "authorization_ref", "correlation_id", "causation_id", "idempotency_key",
        ):
            _required(getattr(self, field), field)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        object.__setattr__(self, "classification", _classification(self.classification))
        object.__setattr__(self, "failure_policy", DelegationFailurePolicy(self.failure_policy))
        object.__setattr__(self, "cancellation_policy", DelegationCancellationPolicy(self.cancellation_policy))
        _aware(self.deadline_at, "deadline_at")
        _aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class DelegationResult:
    delegation_id: str
    child_execution_id: str
    terminal_state: DelegationTerminalState
    result_ref: str | None
    failure_ref: str | None
    handback_ref: HandoffRef | None
    finished_at: datetime
    failure_policy: DelegationFailurePolicy

    def __post_init__(self) -> None:
        _required(self.delegation_id, "delegation_id")
        _required(self.child_execution_id, "child_execution_id")
        object.__setattr__(self, "terminal_state", DelegationTerminalState(self.terminal_state))
        object.__setattr__(self, "failure_policy", DelegationFailurePolicy(self.failure_policy))
        _aware(self.finished_at, "finished_at")
        if self.terminal_state is DelegationTerminalState.COMPLETED:
            if not self.result_ref or self.failure_ref is not None:
                raise ValueError("COMPLETED requires result_ref and no failure_ref")
        elif self.result_ref is not None or not self.failure_ref:
            raise ValueError("non-COMPLETED result requires failure_ref and no result_ref")
        _opaque_ref(self.result_ref, "result_ref") if self.result_ref is not None else None
        _opaque_ref(self.failure_ref, "failure_ref") if self.failure_ref is not None else None


@dataclass(frozen=True, slots=True)
class WaitForDelegations:
    actor: str
    user_id: str
    workspace_id: str | None
    waiting_execution_id: str
    delegation_ids: tuple[str, ...]
    completion_rule: CompletionRule
    minimum_count: int | None
    deadline_at: datetime | None
    purpose: str
    correlation_id: str
    idempotency_key: str
    requested_at: datetime
    expected_version: int | None = None
    allow_failure_refs: bool = False
    checkpoint_ref: str | None = None

    def __post_init__(self) -> None:
        for field in ("actor", "user_id", "waiting_execution_id", "purpose", "correlation_id", "idempotency_key"):
            _required(getattr(self, field), field)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        object.__setattr__(self, "delegation_ids", tuple(self.delegation_ids))
        if not self.delegation_ids or len(self.delegation_ids) > 32:
            raise ValueError("delegation_ids must be bounded and non-empty")
        object.__setattr__(self, "completion_rule", CompletionRule(self.completion_rule))
        if self.completion_rule is CompletionRule.MINIMUM_COUNT:
            if self.minimum_count is None or not 1 <= self.minimum_count <= len(self.delegation_ids):
                raise ValueError("minimum_count must be within delegation_ids")
        elif self.minimum_count is not None:
            raise ValueError("minimum_count is only valid for MINIMUM_COUNT")
        if self.expected_version is not None and self.expected_version < 1:
            raise ValueError("expected_version must be positive")
        if self.checkpoint_ref is not None:
            _required(self.checkpoint_ref, "checkpoint_ref")
        _aware(self.deadline_at, "deadline_at")
        _aware(self.requested_at, "requested_at")


@dataclass(frozen=True, slots=True)
class CancelDelegation:
    actor: str
    user_id: str
    workspace_id: str | None
    delegation_id: str
    target: CancellationScope
    purpose: str
    correlation_id: str
    idempotency_key: str
    requested_at: datetime

    def __post_init__(self) -> None:
        for field in ("actor", "user_id", "delegation_id", "purpose", "correlation_id", "idempotency_key"):
            _required(getattr(self, field), field)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        object.__setattr__(self, "target", CancellationScope(self.target))
        _aware(self.requested_at, "requested_at")


@dataclass(frozen=True, slots=True)
class CreateParticipantAgent:
    actor: str
    user_id: str
    workspace_id: str | None
    owner: str
    agent_command: object
    purpose: str
    correlation_id: str
    idempotency_key: str
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class SendAgentMessage:
    actor: str
    collaboration_id: str
    sender_agent_id: str
    recipient_agent_id: str
    user_id: str
    workspace_id: str | None
    owner: str
    kind: AgentMessageKind
    purpose: str
    classification: DataClassification
    inline_summary: str | None
    content_refs: tuple[str, ...]
    handoff_ref: HandoffRef | None
    deadline_at: datetime | None
    correlation_id: str
    causation_id: str | None
    idempotency_key: str
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class DelegateTask:
    actor: str
    collaboration_id: str
    parent_execution_id: str
    delegator_agent_id: str
    delegate_agent_id: str
    user_id: str
    workspace_id: str | None
    owner: str
    handoff_ref: HandoffRef
    child_limits: ExecutionLimits
    deadline_at: datetime | None
    purpose: str
    classification: DataClassification
    authorization_ref: str
    failure_policy: DelegationFailurePolicy
    cancellation_policy: DelegationCancellationPolicy
    correlation_id: str
    causation_id: str
    idempotency_key: str
    requested_at: datetime
    parent_delegation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReturnDelegationResult:
    actor: str
    delegation_id: str
    result: DelegationResult
    user_id: str
    workspace_id: str | None
    purpose: str
    correlation_id: str
    idempotency_key: str
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class MessageExecutionRef:
    message_id: str
    delivery_execution_id: str


@dataclass(frozen=True, slots=True)
class DelegationReceipt:
    delegation_id: str
    child_execution_id: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class WaitReceipt:
    wait_id: str
    waiting_execution_id: str
    checkpoint_ref: str


@dataclass(frozen=True, slots=True)
class ReturnReceipt:
    delegation_id: str
    accepted: bool
    result: DelegationResult


@dataclass(frozen=True, slots=True)
class CancellationReceipt:
    delegation_id: str
    target: CancellationScope
    requested_execution_ids: tuple[str, ...]
    partial: bool = False


@dataclass(frozen=True, slots=True)
class WaitRegistration:
    wait_id: str
    request: WaitForDelegations
    checkpoint_ref: str
    state: str = "REGISTERED"


__all__ = [name for name in globals() if not name.startswith("_")]
