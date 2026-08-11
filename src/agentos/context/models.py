from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NewType

from agentos.execution.events import DataClassification
from agentos.execution.models import CorrelationId, ExecutionId, UserId, WorkspaceId

ContextCandidateId = NewType("ContextCandidateId", str)
ContextManifestReference = NewType("ContextManifestReference", str)
ContextReference = NewType("ContextReference", str)
PolicyVersion = NewType("PolicyVersion", str)
TokenizerProfile = NewType("TokenizerProfile", str)
SourceReference = NewType("SourceReference", str)
TransformationReference = NewType("TransformationReference", str)


def _required(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")


def _positive(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be positive")


def _non_negative(value: int | Decimal, name: str) -> None:
    if value < 0:
        raise ValueError(f"{name} cannot be negative")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class ContextItemKind(StrEnum):
    SYSTEM_INSTRUCTION = "SYSTEM_INSTRUCTION"
    AGENT_INSTRUCTION = "AGENT_INSTRUCTION"
    TASK = "TASK"
    SUMMARY = "SUMMARY"
    MESSAGE = "MESSAGE"
    MEMORY_REFERENCE = "MEMORY_REFERENCE"
    FILE_REFERENCE = "FILE_REFERENCE"
    DECISION = "DECISION"
    EVENT = "EVENT"
    TOOL_RESULT = "TOOL_RESULT"
    CONTROL_STATE = "CONTROL_STATE"


class ContextPriority(StrEnum):
    REQUIRED = "REQUIRED"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class OverflowPolicy(StrEnum):
    EXCLUDE_OPTIONAL = "EXCLUDE_OPTIONAL"
    REFERENCE_THEN_EXCLUDE = "REFERENCE_THEN_EXCLUDE"
    FAIL_REQUIRED = "FAIL_REQUIRED"


class ContextDisposition(StrEnum):
    DISCARD = "DISCARD"
    PRESERVE_MANIFEST = "PRESERVE_MANIFEST"
    PRESERVE_REFERENCES = "PRESERVE_REFERENCES"


class ContextErrorCategory(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    OWNERSHIP = "OWNERSHIP"
    CLASSIFICATION = "CLASSIFICATION"
    PROVENANCE = "PROVENANCE"
    INTEGRITY = "INTEGRITY"
    SANITIZATION = "SANITIZATION"
    REFERENCE = "REFERENCE"
    BUDGET = "BUDGET"
    TURN_CONFLICT = "TURN_CONFLICT"
    CANCELLED = "CANCELLED"
    FINALIZED = "FINALIZED"
    RECONCILIATION = "RECONCILIATION"


class Retryability(StrEnum):
    NEVER = "NEVER"
    SAFE = "SAFE"
    POLICY_DEPENDENT = "POLICY_DEPENDENT"


class SourceKind(StrEnum):
    TASK = "TASK"
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"
    MEMORY = "MEMORY"
    FILE = "FILE"
    EVENT = "EVENT"
    TOOL = "TOOL"
    DECISION = "DECISION"
    USER = "USER"
    TEST = "TEST"


@dataclass(frozen=True, slots=True)
class ContextOperationContext:
    user_id: UserId
    workspace_id: WorkspaceId | None
    agent_id: str
    execution_id: ExecutionId
    correlation_id: CorrelationId
    purpose: str

    def __post_init__(self) -> None:
        for name, value in (
            ("user_id", self.user_id),
            ("agent_id", self.agent_id),
            ("execution_id", self.execution_id),
            ("correlation_id", self.correlation_id),
            ("purpose", self.purpose),
        ):
            _required(value, name)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")


@dataclass(frozen=True, slots=True)
class OwnershipScope:
    user_id: UserId
    workspace_id: WorkspaceId | None
    agent_id: str
    execution_id: ExecutionId

    def __post_init__(self) -> None:
        for name, value in (
            ("user_id", self.user_id),
            ("agent_id", self.agent_id),
            ("execution_id", self.execution_id),
        ):
            _required(value, name)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")

    @classmethod
    def from_context(cls, context: ContextOperationContext) -> OwnershipScope:
        return cls(context.user_id, context.workspace_id, context.agent_id, context.execution_id)


@dataclass(frozen=True, slots=True)
class ContentReference:
    reference: ContextReference

    def __post_init__(self) -> None:
        _required(self.reference, "reference")


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    reference: ContextReference
    content: str | ContentReference | None = None
    estimated_tokens: int = 1

    def __post_init__(self) -> None:
        _required(self.reference, "reference")
        _non_negative(self.estimated_tokens, "estimated_tokens")


@dataclass(frozen=True, slots=True)
class ContextBudget:
    maximum_input_tokens: int
    reserved_output_tokens: int = 0
    reserved_control_tokens: int = 0
    per_category_limits: tuple[CategoryBudget | tuple[ContextItemKind, int], ...] = ()
    overflow_policy: OverflowPolicy = OverflowPolicy.REFERENCE_THEN_EXCLUDE

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "overflow_policy", OverflowPolicy(self.overflow_policy))
        except (TypeError, ValueError) as exc:
            raise ValueError("overflow_policy is invalid") from exc
        _positive(self.maximum_input_tokens, "maximum_input_tokens")
        _non_negative(self.reserved_output_tokens, "reserved_output_tokens")
        _non_negative(self.reserved_control_tokens, "reserved_control_tokens")
        if self.reserved_control_tokens > self.maximum_input_tokens:
            raise ValueError("reserved_control_tokens cannot exceed maximum_input_tokens")
        for entry in self.per_category_limits:
            kind, limit = (entry.kind, entry.maximum_tokens) if isinstance(entry, CategoryBudget) else entry
            if not isinstance(kind, ContextItemKind):
                raise ValueError("per_category_limits kind must be ContextItemKind")
            _non_negative(limit, "per_category_limit")

    @property
    def available_input_tokens(self) -> int:
        return self.maximum_input_tokens - self.reserved_control_tokens

    def limit_for(self, kind: ContextItemKind) -> int | None:
        for entry in self.per_category_limits:
            entry_kind, limit = (entry.kind, entry.maximum_tokens) if isinstance(entry, CategoryBudget) else entry
            if entry_kind is kind:
                return limit
        return None


@dataclass(frozen=True, slots=True)
class Provenance:
    source_kind: SourceKind | str
    source_ref: SourceReference | str
    source_version: str | None = None
    authored_by: str | None = None
    observed_at: datetime | None = None
    retrieved_at: datetime | None = None
    transformation_chain: tuple[TransformationReference | str, ...] = ()

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "source_kind", SourceKind(self.source_kind))
        except (TypeError, ValueError) as exc:
            raise ValueError("source_kind is invalid") from exc
        _required(str(self.source_kind), "source_kind")
        _required(str(self.source_ref), "source_ref")
        if self.retrieved_at is not None:
            _aware(self.retrieved_at, "retrieved_at")
        if self.observed_at is not None:
            _aware(self.observed_at, "observed_at")
        if self.retrieved_at is None:
            raise ValueError("retrieved_at is required")


@dataclass(frozen=True, slots=True)
class ContextCandidate:
    candidate_id: ContextCandidateId | str
    kind: ContextItemKind
    content: str | ContentReference
    ownership: OwnershipScope
    provenance: Provenance
    classification: DataClassification
    relevance: float
    priority: ContextPriority
    estimated_tokens: int
    created_at: datetime | None = None
    source_version: str | None = None
    integrity_ref: str | None = None
    depends_on: tuple[ContextCandidateId | str, ...] = ()

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "kind", ContextItemKind(self.kind))
            object.__setattr__(self, "priority", ContextPriority(self.priority))
            object.__setattr__(self, "classification", DataClassification(self.classification))
        except (TypeError, ValueError) as exc:
            raise ValueError("context candidate contract enum is invalid") from exc
        _required(self.candidate_id, "candidate_id")
        _non_negative(self.estimated_tokens, "estimated_tokens")
        if self.relevance < 0:
            raise ValueError("relevance cannot be negative")
        if self.created_at is not None:
            _aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class ContextItem:
    candidate_id: ContextCandidateId | str
    kind: ContextItemKind
    content: str | ContentReference
    ownership: OwnershipScope
    provenance: Provenance
    classification: DataClassification
    priority: ContextPriority
    estimated_tokens: int
    untrusted_data: bool
    content_role: str


@dataclass(frozen=True, slots=True)
class CategoryBudget:
    kind: ContextItemKind
    maximum_tokens: int


@dataclass(frozen=True, slots=True)
class TokenAccounting:
    candidate_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    control_tokens: int = 0
    excluded_tokens: int = 0
    transformed_tokens: int = 0
    reserved_output_tokens: int = 0
    reserved_control_tokens: int = 0

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _non_negative(getattr(self, name), name)

    def plus(self, other: TokenAccounting) -> TokenAccounting:
        return TokenAccounting(
            **{name: getattr(self, name) + getattr(other, name) for name in self.__dataclass_fields__}
        )


@dataclass(frozen=True, slots=True)
class IncludedItemRecord:
    candidate_id: ContextCandidateId | str
    kind: ContextItemKind
    reference: ContextReference | None
    estimated_tokens: int
    order: int
    content: None = None


@dataclass(frozen=True, slots=True)
class ExcludedItemRecord:
    candidate_id: ContextCandidateId | str
    kind: ContextItemKind
    reason: str
    reference: ContextReference | None = None
    content: None = None


@dataclass(frozen=True, slots=True)
class ContextTransformation:
    candidate_id: ContextCandidateId | str
    transformation: str
    output_reference: ContextReference | None = None


@dataclass(frozen=True, slots=True)
class ContextPolicySnapshot:
    policy_version: PolicyVersion | str
    tokenizer_profile: TokenizerProfile | str
    source_cutoff_at: datetime
    classification_ceiling: DataClassification
    max_inline_characters: int = 4096
    required_kinds: tuple[ContextItemKind, ...] = ()

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "classification_ceiling", DataClassification(self.classification_ceiling))
        except (TypeError, ValueError) as exc:
            raise ValueError("classification_ceiling is invalid") from exc
        _required(self.policy_version, "policy_version")
        _required(self.tokenizer_profile, "tokenizer_profile")
        _aware(self.source_cutoff_at, "source_cutoff_at")
        _positive(self.max_inline_characters, "max_inline_characters")


@dataclass(frozen=True, slots=True)
class ContextAssemblyRequest:
    context: ContextOperationContext
    turn: int
    task: TaskSnapshot
    model_requirements_ref: str
    budget: ContextBudget
    prior_manifest_ref: ContextManifestReference | None = None

    def __post_init__(self) -> None:
        _positive(self.turn, "turn")
        _required(self.model_requirements_ref, "model_requirements_ref")


@dataclass(frozen=True, slots=True)
class AuthorizedContextQuery:
    context: ContextOperationContext
    cutoff_at: datetime
    classification_ceiling: DataClassification
    allowed_kinds: tuple[ContextItemKind, ...]
    purpose: str


@dataclass(frozen=True, slots=True)
class ContextManifest:
    manifest_id: ContextManifestReference
    execution_id: ExecutionId
    turn: int
    policy_version: PolicyVersion | str
    tokenizer_profile: TokenizerProfile | str
    source_cutoff_at: datetime
    included: tuple[IncludedItemRecord, ...]
    excluded: tuple[ExcludedItemRecord, ...]
    transformations: tuple[ContextTransformation, ...]
    token_accounting: TokenAccounting
    previous_manifest_id: ContextManifestReference | None
    created_at: datetime
    ownership: OwnershipScope | None = None

    def __post_init__(self) -> None:
        _required(self.manifest_id, "manifest_id")
        _required(self.execution_id, "execution_id")
        _positive(self.turn, "turn")
        _aware(self.source_cutoff_at, "source_cutoff_at")
        _aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    execution_id: ExecutionId
    turn: int
    items: tuple[ContextItem, ...]
    token_accounting: TokenAccounting
    context_ref: ContextReference
    manifest_ref: ContextManifestReference
    assembled_at: datetime

    def __post_init__(self) -> None:
        _required(self.execution_id, "execution_id")
        _positive(self.turn, "turn")
        _required(self.context_ref, "context_ref")
        _required(self.manifest_ref, "manifest_ref")
        _aware(self.assembled_at, "assembled_at")


@dataclass(frozen=True, slots=True)
class TurnReference:
    reference: ContextReference | str
    kind: ContextItemKind = ContextItemKind.MESSAGE
    estimated_tokens: int = 1
    classification: DataClassification = DataClassification.INTERNAL

    def __post_init__(self) -> None:
        _required(self.reference, "reference")
        _non_negative(self.estimated_tokens, "estimated_tokens")


@dataclass(frozen=True, slots=True)
class ContextTurnUpdate:
    context: ContextOperationContext
    expected_turn: int
    previous_manifest_ref: ContextManifestReference
    model_message: TurnReference | None = None
    tool_results: tuple[TurnReference, ...] = ()
    new_messages: tuple[TurnReference, ...] = ()
    decisions: tuple[TurnReference, ...] = ()
    observed_events: tuple[TurnReference, ...] = ()
    control_state: TurnReference | None = None
    usage: TokenAccounting = TokenAccounting()

    def __post_init__(self) -> None:
        _positive(self.expected_turn, "expected_turn")
        _required(self.previous_manifest_ref, "previous_manifest_ref")


class ContextError(RuntimeError):
    def __init__(
        self,
        category: ContextErrorCategory,
        code: str,
        retryability: Retryability = Retryability.NEVER,
    ) -> None:
        self.category = category
        self.code = code
        self.retryability = retryability
        super().__init__(code)


__all__ = [
    "AuthorizedContextQuery",
    "CategoryBudget",
    "ContentReference",
    "ContextAssemblyRequest",
    "ContextBudget",
    "ContextCandidate",
    "ContextDisposition",
    "ContextError",
    "ContextErrorCategory",
    "ContextItem",
    "ContextItemKind",
    "ContextManifest",
    "ContextManifestReference",
    "ContextOperationContext",
    "ContextPolicySnapshot",
    "ContextPriority",
    "ContextReference",
    "ContextSnapshot",
    "ContextTransformation",
    "ContextTurnUpdate",
    "DataClassification",
    "ExcludedItemRecord",
    "IncludedItemRecord",
    "OverflowPolicy",
    "OwnershipScope",
    "PolicyVersion",
    "Provenance",
    "Retryability",
    "SourceKind",
    "TaskSnapshot",
    "TokenAccounting",
    "TokenizerProfile",
    "TransformationReference",
    "TurnReference",
]
