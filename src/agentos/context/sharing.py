from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Iterable, Protocol

from .models import DataClassification


_MAX_REFS = 32
_MAX_TEXT = 256


class SharedContextKind(StrEnum):
    TASK = "TASK"
    DECISION = "DECISION"
    MESSAGE = "MESSAGE"
    MEMORY = "MEMORY"
    ARTIFACT = "ARTIFACT"
    BLACKBOARD_ITEM = "BLACKBOARD_ITEM"
    EVENT = "EVENT"
    TOOL_RESULT = "TOOL_RESULT"
    CONTROL_STATE = "CONTROL_STATE"


class ContextShareStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"
    CANCELLED = "CANCELLED"


def _required(value: object, field: str, *, maximum: int = _MAX_TEXT) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds its bound")


def _aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _refs(values: Iterable[object], field: str) -> tuple[object, ...]:
    result = tuple(values)
    if len(result) > _MAX_REFS:
        raise ValueError(f"{field} exceeds its bound")
    return result


def _classification(value: DataClassification) -> DataClassification:
    try:
        return DataClassification(value)
    except ValueError as exc:
        raise ValueError("classification is invalid") from exc


def _classification_allows(ceiling: DataClassification, value: DataClassification) -> bool:
    order = {
        DataClassification.INTERNAL: 0,
        DataClassification.CONFIDENTIAL: 1,
        DataClassification.RESTRICTED: 2,
    }
    return order[_classification(ceiling)] >= order[_classification(value)]


@dataclass(frozen=True, slots=True)
class ContextShareBudget:
    maximum_references: int
    maximum_snapshot_items: int
    maximum_summary_units: int
    maximum_resolved_content_units: int

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            value = getattr(self, field)
            if value < 0:
                raise ValueError(f"{field} cannot be negative")
        if self.maximum_references > _MAX_REFS:
            raise ValueError("maximum_references exceeds its bound")


@dataclass(frozen=True, slots=True)
class ContextShareGrant:
    grant_id: str
    user_id: str
    workspace_id: str | None
    source_agent_id: str
    target_agent_id: str
    source_execution_id: str
    target_execution_id: str
    purpose: str
    classification_ceiling: DataClassification
    budget: ContextShareBudget
    redelegation: bool
    authorization_ref: str
    correlation_id: str
    issued_at: datetime
    expires_at: datetime
    allowed_kinds: tuple[str, ...] = ()
    filters: tuple[object, ...] = ()
    consumption_policy: str = "SINGLE_USE"
    status: ContextShareStatus = ContextShareStatus.ACTIVE
    issued_by: str = ""
    authorization_basis_ref: str = ""
    consumed_at: datetime | None = None
    resolution_count: int = 0
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        for field in (
            "grant_id", "user_id", "source_agent_id", "target_agent_id",
            "source_execution_id", "target_execution_id", "purpose",
            "authorization_ref", "correlation_id",
        ):
            _required(getattr(self, field), field)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        if not isinstance(self.budget, ContextShareBudget):
            raise ValueError("budget must be ContextShareBudget")
        object.__setattr__(self, "classification_ceiling", _classification(self.classification_ceiling))
        _aware(self.issued_at, "issued_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        object.__setattr__(self, "allowed_kinds", tuple(str(kind) for kind in self.allowed_kinds))
        if not self.allowed_kinds:
            object.__setattr__(self, "allowed_kinds", (SharedContextKind.MEMORY.value,))
        object.__setattr__(self, "filters", tuple(self.filters))
        if any(not isinstance(item, ContextShareFilter) for item in self.filters):
            raise ValueError("filters must use canonical ContextShareFilter")
        if self.consumption_policy not in ("SINGLE_USE", "MULTI_USE_UNTIL_TERMINAL"):
            raise ValueError("consumption_policy is invalid")
        object.__setattr__(self, "status", ContextShareStatus(self.status))
        if self.resolution_count < 0:
            raise ValueError("resolution_count cannot be negative")
        if self.issued_by == "":
            object.__setattr__(self, "issued_by", self.source_agent_id)
        if self.authorization_basis_ref == "":
            object.__setattr__(self, "authorization_basis_ref", self.authorization_ref)
        if self.consumed_at is not None:
            _aware(self.consumed_at, "consumed_at")
        if self.revoked_at is not None:
            _aware(self.revoked_at, "revoked_at")


@dataclass(frozen=True, slots=True)
class SharedContextReference:
    shared_ref_id: str
    grant_id: str
    source_kind: str
    source_ref: str
    source_version: int | None
    source_user_id: str
    source_workspace_id: str | None
    source_agent_id: str
    target_agent_id: str
    target_execution_id: str
    purpose: str
    classification: DataClassification
    integrity_ref: str | None
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "shared_ref_id", "grant_id", "source_kind", "source_ref", "source_user_id",
            "source_agent_id", "target_agent_id", "target_execution_id", "purpose",
        ):
            _required(getattr(self, field), field)
        if self.source_workspace_id is not None:
            _required(self.source_workspace_id, "source_workspace_id")
        if self.source_version is not None and self.source_version < 1:
            raise ValueError("source_version must be positive")
        if self.integrity_ref is not None:
            _required(self.integrity_ref, "integrity_ref")
        object.__setattr__(self, "classification", _classification(self.classification))
        _aware(self.created_at, "created_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")

    def validate_against(self, grant: ContextShareGrant, *, now: datetime | None = None) -> None:
        if self.grant_id != grant.grant_id:
            raise ValueError("grant scope does not match reference")
        if self.source_user_id != grant.user_id or (
            self.source_workspace_id != grant.workspace_id
            and not (self.source_workspace_id is None and grant.workspace_id is not None)
        ):
            raise ValueError("reference ownership does not match grant")
        if self.source_agent_id != grant.source_agent_id or self.target_agent_id != grant.target_agent_id:
            raise ValueError("reference Agent scope does not match grant")
        if self.target_execution_id != grant.target_execution_id or self.purpose != grant.purpose:
            raise ValueError("reference purpose or execution does not match grant")
        if not _classification_allows(grant.classification_ceiling, self.classification):
            raise ValueError("classification exceeds grant ceiling")
        if self.expires_at > grant.expires_at:
            raise ValueError("reference expires after grant")
        if now is not None:
            _aware(now, "now")
            if now >= self.expires_at:
                raise ValueError("reference is expired")


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: str | None
    objective: str
    source_version: int
    captured_at: datetime
    integrity_ref: str

    def __post_init__(self) -> None:
        if self.task_id is not None:
            _required(self.task_id, "task_id")
        _required(self.objective, "objective", maximum=512)
        if self.source_version < 1:
            raise ValueError("source_version must be positive")
        _required(self.integrity_ref, "integrity_ref")
        _aware(self.captured_at, "captured_at")


@dataclass(frozen=True, slots=True)
class Criterion:
    criterion_id: str
    description: str
    required: bool = True

    def __post_init__(self) -> None:
        _required(self.criterion_id, "criterion_id")
        _required(self.description, "description", maximum=512)


@dataclass(frozen=True, slots=True)
class Constraint:
    constraint_id: str
    kind: str
    description: str

    def __post_init__(self) -> None:
        _required(self.constraint_id, "constraint_id")
        _required(self.kind, "kind")
        _required(self.description, "description", maximum=512)


@dataclass(frozen=True, slots=True)
class OutputContractRef:
    output_contract_id: str
    version: int
    expected_kind: str
    schema_ref: str | None
    authorization_ref: str
    integrity_ref: str

    def __post_init__(self) -> None:
        _required(self.output_contract_id, "output_contract_id")
        _required(self.expected_kind, "expected_kind")
        _required(self.authorization_ref, "authorization_ref")
        _required(self.integrity_ref, "integrity_ref")
        if self.version < 1:
            raise ValueError("version must be positive")
        if self.schema_ref is not None:
            _required(self.schema_ref, "schema_ref")


@dataclass(frozen=True, slots=True)
class DelegatedGrantRef:
    delegated_grant_id: str
    parent_grant_id: str
    from_agent_id: str
    to_agent_id: str
    target_execution_id: str
    allowed_kinds: tuple[str, ...]
    purpose: str
    redelegation: bool
    expires_at: datetime
    authorization_ref: str
    integrity_ref: str

    def __post_init__(self) -> None:
        for field in (
            "delegated_grant_id", "parent_grant_id", "from_agent_id", "to_agent_id",
            "target_execution_id", "purpose", "authorization_ref", "integrity_ref",
        ):
            _required(getattr(self, field), field)
        object.__setattr__(self, "allowed_kinds", tuple(self.allowed_kinds))
        if not self.allowed_kinds or len(self.allowed_kinds) > _MAX_REFS:
            raise ValueError("allowed_kinds must be bounded and non-empty")
        _aware(self.expires_at, "expires_at")
        if self.redelegation:
            raise ValueError("redelegation is forbidden for this grant")


@dataclass(frozen=True, slots=True)
class StructuredHandoff:
    handoff_id: str
    grant_id: str
    user_id: str
    workspace_id: str | None
    from_agent_id: str
    to_agent_id: str
    source_execution_id: str
    target_execution_id: str
    objective: TaskSnapshot
    success_criteria: tuple[Criterion, ...]
    constraints: tuple[Constraint, ...]
    expected_output: OutputContractRef
    context_refs: tuple[SharedContextReference, ...]
    minimal_snapshot_ref: str | None
    delegated_grant_refs: tuple[DelegatedGrantRef, ...]
    budget: ContextShareBudget
    purpose: str
    classification: DataClassification
    correlation_id: str
    version: int
    integrity_ref: str
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "handoff_id", "grant_id", "user_id", "from_agent_id", "to_agent_id",
            "source_execution_id", "target_execution_id", "purpose", "correlation_id",
            "integrity_ref",
        ):
            _required(getattr(self, field), field)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        if self.version < 1:
            raise ValueError("version must be positive")
        object.__setattr__(self, "success_criteria", tuple(self.success_criteria))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "context_refs", tuple(_refs(self.context_refs, "context_refs")))
        object.__setattr__(self, "delegated_grant_refs", tuple(_refs(self.delegated_grant_refs, "delegated_grant_refs")))
        if len(self.context_refs) > self.budget.maximum_references:
            raise ValueError("handoff references exceed budget")
        object.__setattr__(self, "classification", _classification(self.classification))
        _aware(self.created_at, "created_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        for ref in self.context_refs:
            if not isinstance(ref, SharedContextReference):
                raise ValueError("context_refs must use canonical references")
            if ref.grant_id != self.grant_id or ref.target_agent_id != self.to_agent_id:
                raise ValueError("handoff reference scope does not match handoff")
            if ref.expires_at != self.expires_at:
                raise ValueError("handoff and reference expiration must match")
        for grant in self.delegated_grant_refs:
            if grant.to_agent_id != self.to_agent_id or grant.target_execution_id != self.target_execution_id:
                raise ValueError("delegated grant scope does not match handoff")
            if grant.expires_at > self.expires_at:
                raise ValueError("delegated grant expires after handoff")


@dataclass(frozen=True, slots=True)
class HandoffRef:
    handoff_id: str
    grant_id: str
    from_agent_id: str
    to_agent_id: str
    source_execution_id: str
    target_execution_id: str
    purpose: str
    classification: DataClassification
    version: int
    expires_at: datetime
    integrity_ref: str

    def __post_init__(self) -> None:
        for field in (
            "handoff_id", "grant_id", "from_agent_id", "to_agent_id",
            "source_execution_id", "target_execution_id", "purpose", "integrity_ref",
        ):
            _required(getattr(self, field), field)
        if self.version < 1:
            raise ValueError("version must be positive")
        object.__setattr__(self, "classification", _classification(self.classification))
        _aware(self.expires_at, "expires_at")


@dataclass(frozen=True, slots=True)
class AuthorizedSourceReference:
    source_kind: str
    source_ref: str
    source_version: int | None
    user_id: str
    workspace_id: str | None
    owner_agent_id: str | None
    authorization_ref: str
    permitted_purposes: tuple[str, ...]
    classification: DataClassification
    expires_at: datetime | None
    integrity_ref: str | None

    def __post_init__(self) -> None:
        for field in ("source_kind", "source_ref", "user_id", "authorization_ref"):
            _required(getattr(self, field), field)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        if self.owner_agent_id is not None:
            _required(self.owner_agent_id, "owner_agent_id")
        if self.source_version is not None and self.source_version < 1:
            raise ValueError("source_version must be positive")
        object.__setattr__(self, "permitted_purposes", tuple(self.permitted_purposes))
        if not self.permitted_purposes:
            raise ValueError("permitted_purposes cannot be empty")
        object.__setattr__(self, "classification", _classification(self.classification))
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")
        if self.integrity_ref is not None:
            _required(self.integrity_ref, "integrity_ref")


@dataclass(frozen=True, slots=True)
class ContextShareFilter:
    field: str
    operator: str
    value: object

    def __post_init__(self) -> None:
        if self.field not in {
            "SOURCE_KIND", "SOURCE_VERSION", "SOURCE_AGENT_ID", "SOURCE_EXECUTION_ID",
            "AUTHORED_BY", "CREATED_AT", "OBSERVED_AT", "CLASSIFICATION", "SOURCE_REF",
        }:
            raise ValueError("share filter field is invalid")
        if self.operator not in {"EQUALS", "IN", "BETWEEN", "AT_OR_BEFORE", "AT_OR_AFTER", "AT_MOST"}:
            raise ValueError("share filter operator is invalid")
        if self.field == "CLASSIFICATION":
            _classification(self.value)
        elif self.field in {"CREATED_AT", "OBSERVED_AT"}:
            values = self.value if isinstance(self.value, tuple) else (self.value,)
            if any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None for value in values):
                raise ValueError("temporal share filter value is invalid")
        else:
            values = self.value if isinstance(self.value, (tuple, list)) else (self.value,)
            if any(not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT for value in values):
                raise ValueError("share filter value is invalid")


@dataclass(frozen=True, slots=True)
class AuthorizeContextShare:
    actor: str
    execution_id: str
    user_id: str
    workspace_id: str | None
    source_agent_id: str
    target_agent_id: str
    source_execution_id: str
    target_execution_id: str
    purpose: str
    requested_kinds: tuple[str, ...]
    filters: tuple[ContextShareFilter, ...]
    budget: ContextShareBudget
    classification_ceiling: DataClassification
    consumption_policy: str
    expires_at: datetime
    correlation_id: str
    idempotency_key: str
    authorization_ref: str

    def __post_init__(self) -> None:
        for field in (
            "actor", "execution_id", "user_id", "source_agent_id", "target_agent_id",
            "source_execution_id", "target_execution_id", "purpose", "correlation_id",
            "idempotency_key", "authorization_ref",
        ):
            _required(getattr(self, field), field)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        object.__setattr__(self, "requested_kinds", tuple(str(kind) for kind in self.requested_kinds))
        if not self.requested_kinds:
            raise ValueError("requested_kinds cannot be empty")
        object.__setattr__(self, "filters", tuple(self.filters))
        if any(not isinstance(item, ContextShareFilter) for item in self.filters):
            raise ValueError("filters must use canonical ContextShareFilter")
        object.__setattr__(self, "classification_ceiling", _classification(self.classification_ceiling))
        _aware(self.expires_at, "expires_at")


@dataclass(frozen=True, slots=True)
class CreateSharedContextReference:
    actor: str
    execution_id: str
    user_id: str
    workspace_id: str | None
    source_agent_id: str
    target_agent_id: str
    source_execution_id: str
    target_execution_id: str
    grant_id: str
    source_ref: AuthorizedSourceReference
    source_kind: str
    expected_source_version: int | None
    purpose: str
    correlation_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CreateStructuredHandoff:
    actor: str
    execution_id: str
    user_id: str
    workspace_id: str | None
    source_agent_id: str
    target_agent_id: str
    source_execution_id: str
    target_execution_id: str
    grant_id: str
    objective: TaskSnapshot
    success_criteria: tuple[Criterion, ...]
    constraints: tuple[Constraint, ...]
    expected_output: OutputContractRef | None
    context_refs: tuple[SharedContextReference, ...]
    minimal_snapshot_ref: str | None
    delegated_grant_refs: tuple[DelegatedGrantRef, ...]
    budget: ContextShareBudget
    purpose: str
    correlation_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ResolveSharedContext:
    actor: str
    execution_id: str
    user_id: str
    workspace_id: str | None
    source_agent_id: str
    target_agent_id: str
    source_execution_id: str
    target_execution_id: str
    grant_id: str
    handoff_ref: HandoffRef
    requested_ref_ids: tuple[str, ...]
    purpose: str
    remaining_budget: ContextShareBudget
    expected_resolution_count: int
    correlation_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SharedContextExclusion:
    shared_ref_id: str | None
    source_kind: str
    required: bool
    reason: str
    source_version: int | None


@dataclass(frozen=True, slots=True)
class ResolvedContextSeed:
    grant_id: str
    target_execution_id: str
    authorized_candidates: tuple[SharedContextReference, ...]
    excluded: tuple[SharedContextExclusion, ...]
    policy_version: str
    grant_status: ContextShareStatus
    resolution_count: int
    truncated: bool
    correlation_id: str


@dataclass(frozen=True, slots=True)
class RevokeContextShare:
    actor: str
    execution_id: str
    user_id: str
    workspace_id: str | None
    source_agent_id: str
    target_agent_id: str
    source_execution_id: str
    target_execution_id: str
    grant_id: str
    reason: str
    purpose: str
    correlation_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RevocationReceipt:
    grant_id: str
    previous_status: ContextShareStatus
    status: ContextShareStatus
    target_execution_id: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ExpireContextShare:
    actor: str
    execution_id: str
    user_id: str
    workspace_id: str | None
    source_agent_id: str
    target_agent_id: str
    source_execution_id: str
    target_execution_id: str
    grant_id: str
    policy_cutoff_at: datetime
    reason: str
    purpose: str
    correlation_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ExpirationReceipt:
    grant_id: str
    previous_status: ContextShareStatus
    status: ContextShareStatus
    target_execution_id: str
    correlation_id: str


class ContextSharingService(Protocol):
    """Public marker protocol implemented by the context-sharing adapter."""

    def authorize(self, command): ...

    def create_handoff(self, command) -> HandoffRef: ...

    def resolve(self, query): ...

    def revoke(self, command: RevokeContextShare) -> RevocationReceipt: ...

    def expire(self, command: ExpireContextShare) -> ExpirationReceipt: ...


__all__ = [
    "AuthorizedSourceReference", "AuthorizeContextShare", "Constraint", "ContextShareBudget",
    "ContextShareFilter", "ContextShareGrant", "ContextShareStatus", "ContextSharingService",
    "CreateSharedContextReference", "CreateStructuredHandoff", "Criterion", "DelegatedGrantRef",
    "ExpireContextShare", "ExpirationReceipt", "HandoffRef", "OutputContractRef", "ResolvedContextSeed",
    "ResolveSharedContext", "RevocationReceipt", "RevokeContextShare", "SharedContextExclusion",
    "SharedContextKind", "SharedContextReference", "StructuredHandoff", "TaskSnapshot",
]
