from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Mapping, NewType, TypeAlias

from agentos.events.models import DataClassification, EventEnvelope
from agentos.execution.models import (
    AgentId,
    CorrelationId,
    ExecutionId,
    IdempotencyKey,
    UserId,
    Version,
    WorkspaceId,
)


MemoryId = NewType("MemoryId", str)
MemoryGrantId = NewType("MemoryGrantId", str)
MemoryRevisionId = NewType("MemoryRevisionId", str)
RetentionRunId = NewType("RetentionRunId", str)
ConsolidationId = NewType("ConsolidationId", str)

MAX_MEMORY_CONTENT_CHARS = 4096
MAX_MEMORY_EXCERPT_CHARS = 512
MAX_SEARCH_QUERY_CHARS = 256
MAX_PURPOSE_CHARS = 128
MAX_PROVENANCE_REFS = 32
MAX_TRANSFORMATIONS = 32
MAX_CONSOLIDATION_SOURCES = 32
MAX_RETENTION_REFS = 64
MAX_RESULTS = 100
MAX_CONTENT_UNITS = 4096


def _required(value: object, field: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{field} exceeds its bounded length")
    return value


def _positive(value: int, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field} must be positive")


def _non_negative(value: int, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} cannot be negative")


def _aware(value: datetime | None, field: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field} must be timezone-aware")


def _classification(value: DataClassification) -> DataClassification:
    try:
        return DataClassification(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("classification is invalid") from exc


class MemoryScope(StrEnum):
    PRIVATE = "PRIVATE"
    WORKSPACE = "WORKSPACE"
    USER = "USER"


class MemoryKind(StrEnum):
    EPISODIC = "EPISODIC"
    PROCEDURAL = "PROCEDURAL"
    PREFERENCE = "PREFERENCE"
    FACT = "FACT"
    SEMANTIC = "SEMANTIC"


class MemoryStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class MemorySourceKind(StrEnum):
    USER_STATEMENT = "USER_STATEMENT"
    AGENT_OBSERVATION = "AGENT_OBSERVATION"
    TOOL_RESULT = "TOOL_RESULT"
    ARTIFACT = "ARTIFACT"
    BLACKBOARD_ITEM = "BLACKBOARD_ITEM"
    CONSOLIDATION = "CONSOLIDATION"
    IMPORT = "IMPORT"


class MemoryMatchReason(StrEnum):
    SEMANTIC_RELEVANCE = "SEMANTIC_RELEVANCE"
    TERM_MATCH = "TERM_MATCH"
    FILTER_MATCH = "FILTER_MATCH"
    PROVENANCE_MATCH = "PROVENANCE_MATCH"
    RECENCY = "RECENCY"


class MemorySearchCapability(StrEnum):
    LEXICAL = "LEXICAL"
    HYBRID = "HYBRID"
    SEMANTIC = "SEMANTIC"


class MemoryOperation(StrEnum):
    SAVE = "SAVE"
    READ = "READ"
    SEARCH = "SEARCH"
    INVALIDATE = "INVALIDATE"
    CONSOLIDATE = "CONSOLIDATE"
    RETENTION = "RETENTION"


class MemoryErrorCategory(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    ACCESS_DENIED = "ACCESS_DENIED"
    OWNERSHIP = "OWNERSHIP"
    CLASSIFICATION = "CLASSIFICATION"
    PROVENANCE = "PROVENANCE"
    INTEGRITY = "INTEGRITY"
    REFERENCE = "REFERENCE"
    STATUS = "STATUS"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    BUDGET = "BUDGET"
    RETENTION = "RETENTION"
    CANCELLED = "CANCELLED"
    COMMIT_FAILED = "COMMIT_FAILED"


class MemoryRetryability(StrEnum):
    NEVER = "NEVER"
    SAFE = "SAFE"
    AFTER_RECONCILIATION = "AFTER_RECONCILIATION"


class MemoryCommitState(StrEnum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    UNKNOWN = "UNKNOWN"


class MemoryRetentionOutcome(StrEnum):
    RETAINED = "RETAINED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"


@dataclass(frozen=True, slots=True)
class MemorySearchCapabilities:
    supported: tuple[str, ...]
    adapter_version: str = "memory-search:1"

    def __post_init__(self) -> None:
        normalized = tuple(MemorySearchCapability(value).value for value in self.supported)
        if not normalized:
            raise ValueError("at least one search capability is required")
        object.__setattr__(self, "supported", normalized)
        _required(self.adapter_version, "adapter_version", maximum=64)


@dataclass(frozen=True, slots=True)
class MemoryCitation:
    memory_id: MemoryId | str
    version: Version | int
    source_refs: tuple[str, ...]
    provenance_ref: str
    integrity_ref: str

    def __post_init__(self) -> None:
        _required(self.memory_id, "memory_id")
        _positive(self.version, "version")
        if not self.source_refs:
            raise ValueError("citation requires source_refs")
        for source_ref in self.source_refs:
            _required(source_ref, "source_ref")
        _required(self.provenance_ref, "provenance_ref")
        _required(self.integrity_ref, "integrity_ref")

    def __repr__(self) -> str:
        return f"MemoryCitation(memory_id={self.memory_id!r}, version={self.version}, refs={len(self.source_refs)})"


class MemoryError(RuntimeError):
    def __init__(
        self,
        category: MemoryErrorCategory,
        code: str,
        retryability: MemoryRetryability = MemoryRetryability.NEVER,
    ) -> None:
        self.category = MemoryErrorCategory(category)
        self.code = _required(code, "error code", maximum=96)
        self.retryability = MemoryRetryability(retryability)
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(category={self.category.value!r}, code={self.code!r})"


class MemoryAccessDenied(MemoryError):
    def __init__(self, code: str = "ACCESS_DENIED") -> None:
        super().__init__(MemoryErrorCategory.ACCESS_DENIED, code)


class MemoryNotFound(MemoryAccessDenied):
    def __init__(self) -> None:
        super().__init__("ACCESS_DENIED")


class MemoryValidationError(MemoryError):
    def __init__(self, code: str = "INVALID_REQUEST") -> None:
        super().__init__(MemoryErrorCategory.INVALID_REQUEST, code)


class MemoryVersionConflict(MemoryError):
    def __init__(self, code: str = "VERSION_CONFLICT") -> None:
        super().__init__(MemoryErrorCategory.VERSION_CONFLICT, code)


class MemoryIdempotencyConflict(MemoryError):
    def __init__(self, code: str = "IDEMPOTENCY_CONFLICT") -> None:
        super().__init__(MemoryErrorCategory.IDEMPOTENCY_CONFLICT, code)


class MemoryCommitFailure(MemoryError):
    def __init__(self, code: str = "COMMIT_FAILED") -> None:
        super().__init__(MemoryErrorCategory.COMMIT_FAILED, code, MemoryRetryability.AFTER_RECONCILIATION)


@dataclass(frozen=True, slots=True)
class BoundedMemoryContent:
    value: str

    def __post_init__(self) -> None:
        _required(self.value, "content")
        if len(self.value) > MAX_MEMORY_CONTENT_CHARS:
            raise ValueError("content exceeds its bounded length")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return "BoundedMemoryContent(<opaque>)"


@dataclass(frozen=True, slots=True)
class MemoryArtifactReference:
    artifact_id: str
    version: int
    integrity_ref: str

    def __post_init__(self) -> None:
        _required(self.artifact_id, "artifact_id")
        _positive(self.version, "version")
        _required(self.integrity_ref, "integrity_ref")

    def __repr__(self) -> str:
        return "MemoryArtifactReference(<opaque>)"


MemoryContent: TypeAlias = BoundedMemoryContent | MemoryArtifactReference


@dataclass(frozen=True, slots=True)
class MemoryOperationContext:
    user_id: UserId | str
    workspace_id: WorkspaceId | str | None
    agent_id: AgentId | str
    execution_id: ExecutionId | str
    correlation_id: CorrelationId | str
    purpose: str
    actor: str
    classification_ceiling: DataClassification = DataClassification.RESTRICTED

    def __post_init__(self) -> None:
        _required(self.user_id, "user_id")
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        _required(self.agent_id, "agent_id")
        _required(self.execution_id, "execution_id")
        _required(self.correlation_id, "correlation_id")
        _required(self.purpose, "purpose", maximum=MAX_PURPOSE_CHARS)
        _required(self.actor, "actor", maximum=MAX_PURPOSE_CHARS)
        object.__setattr__(self, "classification_ceiling", _classification(self.classification_ceiling))

    def __repr__(self) -> str:
        return (
            "MemoryOperationContext("
            f"user_id={self.user_id!r}, workspace_id={self.workspace_id!r}, "
            f"agent_id={self.agent_id!r}, execution_id={self.execution_id!r}, "
            f"correlation_id={self.correlation_id!r}, purpose=<bounded>, actor=<bounded>)"
        )


@dataclass(frozen=True, slots=True)
class MemoryProvenance:
    source_kind: MemorySourceKind | str
    source_refs: tuple[str, ...]
    authored_by: str | None = None
    observed_at: datetime | None = None
    confidence: float | None = None
    transformation_chain: tuple[str, ...] = ()
    integrity_ref: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "source_kind", MemorySourceKind(self.source_kind))
        except (TypeError, ValueError) as exc:
            raise ValueError("source_kind is invalid") from exc
        if not self.source_refs or len(self.source_refs) > MAX_PROVENANCE_REFS:
            raise ValueError("source_refs must be bounded and non-empty")
        for source_ref in self.source_refs:
            _required(source_ref, "source_ref")
        if self.authored_by is not None:
            _required(self.authored_by, "authored_by")
        _aware(self.observed_at, "observed_at")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between zero and one")
        if len(self.transformation_chain) > MAX_TRANSFORMATIONS:
            raise ValueError("transformation_chain is too long")
        for transformation in self.transformation_chain:
            _required(transformation, "transformation")
        _required(self.integrity_ref, "integrity_ref")

    def __repr__(self) -> str:
        return f"MemoryProvenance(source_kind={self.source_kind.value!r}, refs={len(self.source_refs)})"


@dataclass(frozen=True, slots=True)
class MemoryReference:
    memory_id: MemoryId | str
    version: Version | int
    user_id: UserId | str
    workspace_id: WorkspaceId | str | None
    permitted_agent_id: AgentId | str | None
    authorization_ref: str
    purpose: str
    expires_at: datetime | None
    integrity_ref: str

    def __post_init__(self) -> None:
        _required(self.memory_id, "memory_id")
        _positive(self.version, "version")
        _required(self.user_id, "user_id")
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        if self.permitted_agent_id is not None:
            _required(self.permitted_agent_id, "permitted_agent_id")
        _required(self.authorization_ref, "authorization_ref")
        _required(self.purpose, "purpose", maximum=MAX_PURPOSE_CHARS)
        _aware(self.expires_at, "expires_at")
        _required(self.integrity_ref, "integrity_ref")

    def __repr__(self) -> str:
        return f"MemoryReference(memory_id={self.memory_id!r}, version={self.version}, authorization_ref=<opaque>)"


@dataclass(frozen=True, slots=True)
class MemoryRevision:
    memory_id: MemoryId | str
    version: Version | int
    previous_version: Version | int | None
    changed_by: str
    execution_id: ExecutionId | str
    correlation_id: CorrelationId | str
    change_reason: str
    changed_at: datetime

    def __post_init__(self) -> None:
        _required(self.memory_id, "memory_id")
        _positive(self.version, "version")
        if self.previous_version is not None:
            _positive(self.previous_version, "previous_version")
        _required(self.changed_by, "changed_by")
        _required(self.execution_id, "execution_id")
        _required(self.correlation_id, "correlation_id")
        _required(self.change_reason, "change_reason", maximum=96)
        _aware(self.changed_at, "changed_at")


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: MemoryId | str
    user_id: UserId | str
    workspace_id: WorkspaceId | str | None
    owner_agent_id: AgentId | str | None
    scope: MemoryScope
    base_scope: MemoryScope
    kind: MemoryKind
    content: MemoryContent
    provenance: MemoryProvenance
    classification: DataClassification
    retention_policy_ref: str
    status: MemoryStatus
    version: Version | int
    created_by: str
    created_execution_id: ExecutionId | str
    correlation_id: CorrelationId | str
    created_at: datetime
    valid_from: datetime
    expires_at: datetime | None = None
    invalidated_at: datetime | None = None
    superseded_by: MemoryId | str | None = None
    lineage: tuple[MemoryReference, ...] = ()

    def __post_init__(self) -> None:
        _required(self.memory_id, "memory_id")
        _required(self.user_id, "user_id")
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        if self.owner_agent_id is not None:
            _required(self.owner_agent_id, "owner_agent_id")
        try:
            object.__setattr__(self, "scope", MemoryScope(self.scope))
            object.__setattr__(self, "base_scope", MemoryScope(self.base_scope))
            object.__setattr__(self, "kind", MemoryKind(self.kind))
            object.__setattr__(self, "status", MemoryStatus(self.status))
            object.__setattr__(self, "classification", _classification(self.classification))
        except (TypeError, ValueError) as exc:
            raise ValueError("memory enum is invalid") from exc
        if self.scope is MemoryScope.PRIVATE and not self.owner_agent_id:
            raise ValueError("PRIVATE memory requires owner_agent_id")
        if self.scope is MemoryScope.WORKSPACE and not self.workspace_id:
            raise ValueError("WORKSPACE memory requires workspace_id")
        if self.scope is MemoryScope.USER and self.workspace_id is not None:
            raise ValueError("USER memory cannot have workspace_id")
        if self.base_scope is not self.scope:
            raise ValueError("base_scope must equal the ownership scope")
        if isinstance(self.content, str):
            object.__setattr__(self, "content", BoundedMemoryContent(self.content))
        if isinstance(self.content, BoundedMemoryContent):
            pass
        elif not isinstance(self.content, MemoryArtifactReference):
            raise ValueError("content must be bounded text or MemoryArtifactReference")
        _required(self.retention_policy_ref, "retention_policy_ref")
        _positive(self.version, "version")
        _required(self.created_by, "created_by")
        _required(self.created_execution_id, "created_execution_id")
        _required(self.correlation_id, "correlation_id")
        _aware(self.created_at, "created_at")
        _aware(self.valid_from, "valid_from")
        _aware(self.expires_at, "expires_at")
        _aware(self.invalidated_at, "invalidated_at")
        if self.status is MemoryStatus.SUPERSEDED and not self.superseded_by:
            raise ValueError("SUPERSEDED memory requires superseded_by")
        if self.status in (MemoryStatus.INVALIDATED, MemoryStatus.EXPIRED) and self.invalidated_at is None:
            raise ValueError("terminal memory requires invalidated_at")
        if len(self.lineage) > MAX_CONSOLIDATION_SOURCES:
            raise ValueError("lineage is too long")

    def __repr__(self) -> str:
        return (
            "MemoryRecord("
            f"memory_id={self.memory_id!r}, scope={self.scope.value!r}, "
            f"kind={self.kind.value!r}, status={self.status.value!r}, version={self.version})"
        )


@dataclass(frozen=True, slots=True)
class MemoryGrant:
    grant_id: MemoryGrantId | str
    memory_id: MemoryId | str
    user_id: UserId | str
    source_agent_id: AgentId | str
    target_agent_id: AgentId | str
    target_execution_id: ExecutionId | str
    purpose: str
    classification_ceiling: DataClassification
    expires_at: datetime
    maximum_uses: int
    redelegation: bool = False
    revoked: bool = False
    uses: int = 0

    def __post_init__(self) -> None:
        for field in (
            "grant_id", "memory_id", "user_id", "source_agent_id", "target_agent_id", "target_execution_id"
        ):
            _required(getattr(self, field), field)
        _required(self.purpose, "purpose", maximum=MAX_PURPOSE_CHARS)
        object.__setattr__(self, "classification_ceiling", _classification(self.classification_ceiling))
        _aware(self.expires_at, "expires_at")
        _positive(self.maximum_uses, "maximum_uses")
        _non_negative(self.uses, "uses")
        if self.uses > self.maximum_uses:
            raise ValueError("grant uses cannot exceed maximum_uses")
        if self.redelegation:
            raise ValueError("grant redelegation is forbidden")


@dataclass(frozen=True, slots=True)
class BoundedSearchIntent:
    text: str

    def __post_init__(self) -> None:
        _required(self.text, "query", maximum=MAX_SEARCH_QUERY_CHARS)

    def __repr__(self) -> str:
        return "BoundedSearchIntent(<bounded>)"


@dataclass(frozen=True, slots=True)
class MemoryFilter:
    scopes: tuple[MemoryScope, ...] = ()
    kinds: tuple[MemoryKind, ...] = ()
    statuses: tuple[MemoryStatus, ...] = (MemoryStatus.ACTIVE,)
    source_kinds: tuple[MemorySourceKind, ...] = ()
    authored_by: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    created_from: datetime | None = None
    created_to: datetime | None = None
    valid_at: datetime | None = None
    minimum_confidence: float | None = None
    classification_ceiling: DataClassification = DataClassification.RESTRICTED

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "scopes", tuple(MemoryScope(value) for value in self.scopes))
            object.__setattr__(self, "kinds", tuple(MemoryKind(value) for value in self.kinds))
            object.__setattr__(self, "statuses", tuple(MemoryStatus(value) for value in self.statuses))
            object.__setattr__(self, "source_kinds", tuple(MemorySourceKind(value) for value in self.source_kinds))
            object.__setattr__(self, "classification_ceiling", _classification(self.classification_ceiling))
        except (TypeError, ValueError) as exc:
            raise ValueError("memory filter enum is invalid") from exc
        for value in (*self.authored_by, *self.source_refs):
            _required(value, "memory filter value")
        _aware(self.created_from, "created_from")
        _aware(self.created_to, "created_to")
        _aware(self.valid_at, "valid_at")
        if self.minimum_confidence is not None and not 0.0 <= self.minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class SaveMemory:
    context: MemoryOperationContext
    scope: MemoryScope
    kind: MemoryKind
    content: MemoryContent
    provenance: MemoryProvenance
    classification: DataClassification
    retention_policy_ref: str
    idempotency_key: IdempotencyKey | str
    memory_ref: MemoryReference | None = None
    expected_version: Version | int | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", MemoryScope(self.scope))
        object.__setattr__(self, "kind", MemoryKind(self.kind))
        object.__setattr__(self, "classification", _classification(self.classification))
        if isinstance(self.content, str):
            object.__setattr__(self, "content", BoundedMemoryContent(self.content))
        _required(self.retention_policy_ref, "retention_policy_ref")
        _required(self.idempotency_key, "idempotency_key")
        _aware(self.expires_at, "expires_at")
        if self.memory_ref is None and self.expected_version is not None:
            raise ValueError("expected_version requires memory_ref")
        if self.memory_ref is not None and self.expected_version is None:
            raise ValueError("update requires expected_version")


@dataclass(frozen=True, slots=True)
class GetMemory:
    context: MemoryOperationContext
    memory_ref: MemoryReference
    classification_ceiling: DataClassification = DataClassification.RESTRICTED

    def __post_init__(self) -> None:
        object.__setattr__(self, "classification_ceiling", _classification(self.classification_ceiling))


@dataclass(frozen=True, slots=True)
class SearchMemory:
    context: MemoryOperationContext
    allowed_scopes: tuple[MemoryScope, ...]
    query: BoundedSearchIntent
    filters: tuple[MemoryFilter, ...] = ()
    maximum_results: int = 20
    maximum_content_units: int = 512
    classification_ceiling: DataClassification = DataClassification.RESTRICTED
    grant_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_scopes", tuple(MemoryScope(value) for value in self.allowed_scopes))
        object.__setattr__(self, "classification_ceiling", _classification(self.classification_ceiling))
        if not self.allowed_scopes:
            raise ValueError("allowed_scopes cannot be empty")
        if self.maximum_results < 1 or self.maximum_results > MAX_RESULTS:
            raise ValueError("maximum_results is out of bounds")
        if self.maximum_content_units < 1 or self.maximum_content_units > MAX_CONTENT_UNITS:
            raise ValueError("maximum_content_units is out of bounds")
        for grant_ref in self.grant_refs:
            _required(grant_ref, "grant_ref")


@dataclass(frozen=True, slots=True)
class InvalidateMemory:
    context: MemoryOperationContext
    memory_ref: MemoryReference
    expected_version: Version | int
    reason: str
    idempotency_key: IdempotencyKey | str

    def __post_init__(self) -> None:
        _positive(self.expected_version, "expected_version")
        _required(self.reason, "reason", maximum=96)
        _required(self.idempotency_key, "idempotency_key")


@dataclass(frozen=True, slots=True)
class ConsolidateMemory:
    context: MemoryOperationContext
    source_refs: tuple[MemoryReference, ...]
    target_scope: MemoryScope
    target_kind: MemoryKind
    content: MemoryContent
    provenance: MemoryProvenance
    retention_policy_ref: str
    idempotency_key: IdempotencyKey | str
    supersede_sources: bool = False

    def __post_init__(self) -> None:
        if not self.source_refs or len(self.source_refs) > MAX_CONSOLIDATION_SOURCES:
            raise ValueError("source_refs are out of bounds")
        object.__setattr__(self, "target_scope", MemoryScope(self.target_scope))
        object.__setattr__(self, "target_kind", MemoryKind(self.target_kind))
        _required(self.retention_policy_ref, "retention_policy_ref")
        _required(self.idempotency_key, "idempotency_key")


@dataclass(frozen=True, slots=True)
class ApplyMemoryRetention:
    context: MemoryOperationContext
    scope: MemoryScope
    memory_refs: tuple[MemoryReference, ...]
    retention_policy_ref: str
    policy_cutoff_at: datetime
    idempotency_key: IdempotencyKey | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", MemoryScope(self.scope))
        if not self.memory_refs or len(self.memory_refs) > MAX_RETENTION_REFS:
            raise ValueError("memory_refs are out of bounds")
        _required(self.retention_policy_ref, "retention_policy_ref")
        _aware(self.policy_cutoff_at, "policy_cutoff_at")
        _required(self.idempotency_key, "idempotency_key")


@dataclass(frozen=True, slots=True)
class MemoryWriteReceipt:
    memory_id: MemoryId | str
    version: Version | int
    status: MemoryStatus
    correlation_id: CorrelationId | str
    event_id: str
    already_applied: bool = False

    def __post_init__(self) -> None:
        _required(self.memory_id, "memory_id")
        _positive(self.version, "version")
        object.__setattr__(self, "status", MemoryStatus(self.status))
        _required(self.correlation_id, "correlation_id")
        _required(self.event_id, "event_id")


@dataclass(frozen=True, slots=True)
class AuthorizedMemory:
    memory_ref: MemoryReference
    version: Version | int
    content: MemoryContent
    provenance: MemoryProvenance
    classification: DataClassification
    status: MemoryStatus
    authorized_scope: MemoryScope
    purpose: str
    policy_version: str
    retrieved_at: datetime
    correlation_id: CorrelationId | str

    def __post_init__(self) -> None:
        _positive(self.version, "version")
        if self.status is not MemoryStatus.ACTIVE:
            raise ValueError("AuthorizedMemory must be ACTIVE")
        object.__setattr__(self, "classification", _classification(self.classification))
        object.__setattr__(self, "authorized_scope", MemoryScope(self.authorized_scope))
        _required(self.purpose, "purpose", maximum=MAX_PURPOSE_CHARS)
        _required(self.policy_version, "policy_version")
        _aware(self.retrieved_at, "retrieved_at")
        _required(self.correlation_id, "correlation_id")

    def __repr__(self) -> str:
        return f"AuthorizedMemory(memory_id={self.memory_ref.memory_id!r}, version={self.version}, status='ACTIVE')"


@dataclass(frozen=True, slots=True)
class MemoryMatch:
    memory_ref: MemoryReference
    version: Version | int
    kind: MemoryKind
    scope: MemoryScope
    excerpt: str | None
    relevance: float
    match_reasons: tuple[MemoryMatchReason, ...]
    provenance: MemoryProvenance
    classification: DataClassification
    citation: MemoryCitation | None = None

    def __post_init__(self) -> None:
        _positive(self.version, "version")
        object.__setattr__(self, "kind", MemoryKind(self.kind))
        object.__setattr__(self, "scope", MemoryScope(self.scope))
        object.__setattr__(self, "classification", _classification(self.classification))
        if self.excerpt is not None and len(self.excerpt) > MAX_MEMORY_EXCERPT_CHARS:
            raise ValueError("excerpt exceeds its bounded length")
        if self.relevance < 0:
            raise ValueError("relevance cannot be negative")
        object.__setattr__(self, "match_reasons", tuple(MemoryMatchReason(reason) for reason in self.match_reasons))
        if self.citation is None:
            object.__setattr__(
                self,
                "citation",
                MemoryCitation(
                    memory_id=self.memory_ref.memory_id,
                    version=self.version,
                    source_refs=self.provenance.source_refs,
                    provenance_ref=self.provenance.integrity_ref or "provenance:memory",
                    integrity_ref=self.memory_ref.integrity_ref,
                ),
            )

    def __repr__(self) -> str:
        return (
            "MemoryMatch("
            f"memory_id={self.memory_ref.memory_id!r}, version={self.version}, "
            f"relevance={self.relevance!r}, reasons={len(self.match_reasons)})"
        )


@dataclass(frozen=True, slots=True)
class MemorySearchResult:
    matches: tuple[MemoryMatch, ...]
    applied_scope: tuple[MemoryScope, ...]
    policy_version: str
    truncated: bool
    correlation_id: CorrelationId | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "matches", tuple(self.matches))
        object.__setattr__(self, "applied_scope", tuple(MemoryScope(scope) for scope in self.applied_scope))
        _required(self.policy_version, "policy_version")
        _required(self.correlation_id, "correlation_id")


@dataclass(frozen=True, slots=True)
class MemoryConsolidationReceipt:
    consolidation_id: ConsolidationId | str
    output_memory_id: MemoryId | str
    output_version: Version | int
    source_memory_ids: tuple[MemoryId | str, ...]
    source_versions: tuple[Version | int, ...]
    status: str
    execution_id: ExecutionId | str
    correlation_id: CorrelationId | str
    event_id: str
    already_applied: bool = False

    def __post_init__(self) -> None:
        _required(self.consolidation_id, "consolidation_id")
        _required(self.output_memory_id, "output_memory_id")
        _positive(self.output_version, "output_version")
        _required(self.status, "status")
        _required(self.execution_id, "execution_id")
        _required(self.correlation_id, "correlation_id")
        _required(self.event_id, "event_id")


@dataclass(frozen=True, slots=True)
class RetentionReceipt:
    retention_run_id: RetentionRunId | str
    evaluated_count: int
    expired_count: int
    invalidated_count: int
    retained_count: int
    policy_version: str
    execution_id: ExecutionId | str
    correlation_id: CorrelationId | str
    event_id: str
    already_applied: bool = False

    def __post_init__(self) -> None:
        _required(self.retention_run_id, "retention_run_id")
        for field in ("evaluated_count", "expired_count", "invalidated_count", "retained_count"):
            _non_negative(getattr(self, field), field)
        _required(self.policy_version, "policy_version")
        _required(self.execution_id, "execution_id")
        _required(self.correlation_id, "correlation_id")
        _required(self.event_id, "event_id")


@dataclass(frozen=True, slots=True)
class MemoryAuditRecord:
    audit_id: str
    operation: MemoryOperation
    context: MemoryOperationContext
    outcome: str
    memory_ids: tuple[str, ...]
    versions: tuple[int, ...]
    scope: MemoryScope | None
    reason: str
    event_id: str
    classification: DataClassification = DataClassification.INTERNAL

    def __post_init__(self) -> None:
        _required(self.audit_id, "audit_id")
        object.__setattr__(self, "operation", MemoryOperation(self.operation))
        _required(self.outcome, "outcome", maximum=96)
        for memory_id in self.memory_ids:
            _required(memory_id, "memory_id")
        for version in self.versions:
            _positive(version, "version")
        if self.scope is not None:
            object.__setattr__(self, "scope", MemoryScope(self.scope))
        _required(self.reason, "reason", maximum=96)
        _required(self.event_id, "event_id")
        object.__setattr__(self, "classification", _classification(self.classification))


@dataclass(frozen=True, slots=True)
class MemoryCommitChange:
    record: MemoryRecord
    expected_version: int | None
    revision: MemoryRevision


@dataclass(frozen=True, slots=True)
class MemoryCommitRequest:
    operation: MemoryOperation
    context: MemoryOperationContext
    idempotency_key: str
    fingerprint: str
    changes: tuple[MemoryCommitChange, ...]
    audit: MemoryAuditRecord
    event: EventEnvelope
    result: Any
    additional_events: tuple[EventEnvelope, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", MemoryOperation(self.operation))
        _required(self.idempotency_key, "idempotency_key")
        _required(self.fingerprint, "fingerprint")


@dataclass(frozen=True, slots=True)
class MemoryCommitResult:
    applied: bool
    already_applied: bool
    result: Any
    event_id: str
    commit_state: MemoryCommitState = MemoryCommitState.COMMITTED
    transaction_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "commit_state", MemoryCommitState(self.commit_state))
        _required(self.event_id, "event_id")
        if self.transaction_id is not None:
            _required(self.transaction_id, "transaction_id")


def stable_fingerprint(value: Mapping[str, object]) -> str:
    canonical = repr(tuple(sorted((str(key), repr(item)) for key, item in value.items())))
    return sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "AuthorizedMemory",
    "ApplyMemoryRetention",
    "BoundedMemoryContent",
    "BoundedSearchIntent",
    "ConsolidateMemory",
    "ConsolidationId",
    "GetMemory",
    "InvalidateMemory",
    "MAX_CONTENT_UNITS",
    "MAX_MEMORY_CONTENT_CHARS",
    "MAX_MEMORY_EXCERPT_CHARS",
    "MAX_RESULTS",
    "MemoryAccessDenied",
    "MemoryArtifactReference",
    "MemoryAuditRecord",
    "MemoryCommitChange",
    "MemoryCommitFailure",
    "MemoryCommitRequest",
    "MemoryCommitResult",
    "MemoryCommitState",
    "MemoryConsolidationReceipt",
    "MemoryContent",
    "MemoryCitation",
    "MemoryError",
    "MemoryErrorCategory",
    "MemoryFilter",
    "MemoryGrant",
    "MemoryId",
    "MemoryIdempotencyConflict",
    "MemoryKind",
    "MemoryMatch",
    "MemoryMatchReason",
    "MemoryNotFound",
    "MemoryOperation",
    "MemoryOperationContext",
    "MemoryProvenance",
    "MemoryRecord",
    "MemoryReference",
    "MemoryRetentionOutcome",
    "MemoryRetryability",
    "MemoryRevision",
    "MemoryScope",
    "MemorySearchResult",
    "MemorySearchCapability",
    "MemorySearchCapabilities",
    "MemorySourceKind",
    "MemoryStatus",
    "MemoryValidationError",
    "MemoryVersionConflict",
    "MemoryWriteReceipt",
    "RetentionReceipt",
    "RetentionRunId",
    "SaveMemory",
    "SearchMemory",
    "stable_fingerprint",
]
