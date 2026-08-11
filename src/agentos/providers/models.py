from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, NewType

from agentos.execution.events import DataClassification
from agentos.execution.models import (
    AgentId,
    CancellationReason,
    CorrelationId,
    ExecutionId,
    IdempotencyKey,
    UserId,
    WorkspaceId,
)


def _required(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")


def _positive(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be positive")


def _non_negative(value: int | Decimal, name: str) -> None:
    if value < 0:
        raise ValueError(f"{name} cannot be negative")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class OpaqueRef(str):
    def __new__(cls, value: str) -> OpaqueRef:
        _required(value, cls.__name__)
        return str.__new__(cls, value)


class ProviderRef(OpaqueRef): ...
class ModelRef(OpaqueRef): ...
class ProviderModelBindingRef(OpaqueRef): ...
class ProviderRevision(OpaqueRef): ...
class ModelRevision(OpaqueRef): ...
class CatalogVersion(OpaqueRef): ...
class ModelPolicyVersion(OpaqueRef): ...
class ProfileRevision(OpaqueRef): ...
class PricingRevisionRef(OpaqueRef): ...
class AvailabilitySnapshotRef(OpaqueRef): ...
class SelectionRef(OpaqueRef): ...
class ModelSelectionRef(SelectionRef): ...
class ModelSelectionId(OpaqueRef): ...
class ApprovedModelRequirementsRef(OpaqueRef): ...
class IntegrityRef(OpaqueRef): ...
class FallbackPolicyRef(OpaqueRef): ...
class ProviderInvocationId(OpaqueRef): ...
class ProviderStreamId(OpaqueRef): ...
class ProviderTerminalRef(OpaqueRef): ...
class ProviderCancelRequestId(OpaqueRef): ...
class InternalDiagnosticRef(OpaqueRef): ...
class CancellationSignalRef(OpaqueRef): ...
class ResultReference(OpaqueRef): ...
class ImageReference(OpaqueRef): ...
class ToolRef(OpaqueRef): ...
class ToolCallId(OpaqueRef): ...
class PublicErrorCode(OpaqueRef): ...
class IdempotencyKeyRef(OpaqueRef): ...
class Purpose(OpaqueRef): ...


class ProviderStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"
    RETIRED = "RETIRED"


class ModelStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    DISABLED = "DISABLED"
    RETIRED = "RETIRED"


class Measurement(StrEnum):
    CONFIRMED = "CONFIRMED"
    ESTIMATED = "ESTIMATED"
    COMPUTED_FROM_CATALOG = "COMPUTED_FROM_CATALOG"
    UNAVAILABLE = "UNAVAILABLE"


class Retryability(StrEnum):
    NEVER = "NEVER"
    SAFE = "SAFE"
    POLICY_DEPENDENT = "POLICY_DEPENDENT"


class CancellationRequirement(StrEnum):
    ANY = "ANY"
    LOCAL_OR_REMOTE = "LOCAL_OR_REMOTE"
    REMOTE_REQUIRED = "REMOTE_REQUIRED"


class CancellationMode(StrEnum):
    COOPERATIVE_REMOTE = "COOPERATIVE_REMOTE"
    LOCAL_ONLY = "LOCAL_ONLY"
    UNSUPPORTED = "UNSUPPORTED"


class FallbackMode(StrEnum):
    DISABLED = "DISABLED"
    EXPLICIT_ORDER = "EXPLICIT_ORDER"
    POLICY = "POLICY"


class ModelRole(StrEnum):
    PRIMARY = "PRIMARY"
    FALLBACK = "FALLBACK"


class ModelProfile(StrEnum):
    CODING = "CODING"
    REASONING = "REASONING"
    ORCHESTRATOR = "ORCHESTRATOR"
    VISION = "VISION"
    CHEAP = "CHEAP"
    BALANCED = "BALANCED"


class InputKind(StrEnum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    TOOL_RESULT = "TOOL_RESULT"


class ResponseFormat(StrEnum):
    TEXT = "TEXT"
    JSON = "JSON"
    SCHEMA_CONSTRAINED = "SCHEMA_CONSTRAINED"


class SamplingParameter(StrEnum):
    TEMPERATURE = "TEMPERATURE"
    TOP_P = "TOP_P"
    STOP = "STOP"
    SEED = "SEED"


class RequiredCapability(StrEnum):
    VISION = "VISION"
    TOOLS = "TOOLS"
    STREAMING = "STREAMING"
    STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"


class ConstraintCode(StrEnum):
    OWNERSHIP = "OWNERSHIP"
    PROVIDER_STATUS = "PROVIDER_STATUS"
    MODEL_STATUS = "MODEL_STATUS"
    DATA_CLASSIFICATION = "DATA_CLASSIFICATION"
    REGION = "REGION"
    CONTEXT_LIMIT = "CONTEXT_LIMIT"
    INPUT_KIND = "INPUT_KIND"
    RESPONSE_FORMAT = "RESPONSE_FORMAT"
    CAPABILITY = "CAPABILITY"
    CANCELLATION = "CANCELLATION"
    PROVIDER_DENIED = "PROVIDER_DENIED"
    PROVIDER_NOT_ALLOWED = "PROVIDER_NOT_ALLOWED"
    MODEL_NOT_ALLOWED = "MODEL_NOT_ALLOWED"
    COST_UNKNOWN = "COST_UNKNOWN"
    BUDGET = "BUDGET"
    AVAILABILITY_EXPIRED = "AVAILABILITY_EXPIRED"
    PURPOSE = "PURPOSE"


class ProviderErrorCategory(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    CONTEXT_LIMIT = "CONTEXT_LIMIT"
    CONTENT_REJECTED = "CONTENT_REJECTED"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    TIMEOUT = "TIMEOUT"
    CONNECTION = "CONNECTION"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    CANCELLED = "CANCELLED"
    PROVIDER_INTERNAL = "PROVIDER_INTERNAL"
    POLICY_REJECTED = "POLICY_REJECTED"
    INDETERMINATE = "INDETERMINATE"
    UNKNOWN = "UNKNOWN"


class FinishReason(StrEnum):
    STOP = "STOP"
    LENGTH = "LENGTH"
    TOOL_CALLS = "TOOL_CALLS"
    CONTENT_FILTER = "CONTENT_FILTER"
    REFUSAL = "REFUSAL"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class RequestAcceptance(StrEnum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


class AccountingFinality(StrEnum):
    FINAL_CONFIRMED = "FINAL_CONFIRMED"
    FINAL_ESTIMATED = "FINAL_ESTIMATED"
    FINAL_UNAVAILABLE = "FINAL_UNAVAILABLE"


class ProviderTerminalState(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ProviderOperationContext:
    user_id: UserId
    workspace_id: WorkspaceId | None
    agent_id: AgentId | str
    execution_id: ExecutionId
    correlation_id: CorrelationId
    purpose: Purpose | str
    actor_ref: str

    def __post_init__(self) -> None:
        for name, value in (("user_id", self.user_id), ("agent_id", self.agent_id),
                            ("execution_id", self.execution_id), ("correlation_id", self.correlation_id),
                            ("purpose", self.purpose), ("actor_ref", self.actor_ref)):
            _required(value, name)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")


ModelCatalogOperationContext = ProviderOperationContext


def same_scope(left: ProviderOperationContext, right: ProviderOperationContext) -> bool:
    return left == right


@dataclass(frozen=True, slots=True)
class ProviderModelBinding:
    binding_ref: ProviderModelBindingRef
    provider_ref: ProviderRef
    revision: ProviderRevision


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    provider_ref: ProviderRef
    name: str = field(repr=False)
    status: ProviderStatus = ProviderStatus.ACTIVE
    supported_data_classifications: tuple[DataClassification, ...] = ()
    allowed_regions: tuple[str, ...] = ()
    revision: ProviderRevision = ProviderRevision("provider-revision:1")
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.name, "name")
        if not isinstance(self.status, ProviderStatus):
            object.__setattr__(self, "status", ProviderStatus(self.status))
        if self.created_at is not None:
            _aware(self.created_at, "created_at")
        if self.updated_at is not None:
            _aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class ModelContextLimits:
    maximum_total_tokens: int
    maximum_input_tokens: int
    maximum_output_tokens: int
    tokenizer_profile: str

    def __post_init__(self) -> None:
        for name, value in (("maximum_total_tokens", self.maximum_total_tokens),
                            ("maximum_input_tokens", self.maximum_input_tokens),
                            ("maximum_output_tokens", self.maximum_output_tokens)):
            _positive(value, name)
        _required(self.tokenizer_profile, "tokenizer_profile")


@dataclass(frozen=True, slots=True)
class ModelCost:
    currency: str = "USD"
    input_per_million_tokens: Decimal | None = None
    output_per_million_tokens: Decimal | None = None
    cached_input_per_million_tokens: Decimal | None = None
    image_unit_cost: Decimal | None = None
    minimum_charge: Decimal | None = None
    pricing_revision: PricingRevisionRef = PricingRevisionRef("pricing:unknown")
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    measurement_basis: Measurement = Measurement.UNAVAILABLE

    def __post_init__(self) -> None:
        _required(self.currency, "currency")
        if not isinstance(self.measurement_basis, Measurement):
            object.__setattr__(self, "measurement_basis", Measurement(self.measurement_basis))
        for name, value in (("input_per_million_tokens", self.input_per_million_tokens),
                            ("output_per_million_tokens", self.output_per_million_tokens),
                            ("cached_input_per_million_tokens", self.cached_input_per_million_tokens),
                            ("image_unit_cost", self.image_unit_cost), ("minimum_charge", self.minimum_charge)):
            if value is not None:
                _non_negative(value, name)
        if self.effective_from is not None:
            _aware(self.effective_from, "effective_from")
        if self.effective_until is not None:
            _aware(self.effective_until, "effective_until")
            if self.effective_from is not None and self.effective_until <= self.effective_from:
                raise ValueError("effective_until must be after effective_from")

    @property
    def comparable(self) -> bool:
        return self.input_per_million_tokens is not None and self.output_per_million_tokens is not None


@dataclass(frozen=True, slots=True)
class VisionCapability:
    supported: bool = False
    media_types: tuple[str, ...] = ()
    maximum_images: int | None = None
    maximum_image_bytes: int | None = None
    detail_modes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolCapability:
    supported: bool = False
    parallel_calls: bool = False
    strict_schema: bool = False
    maximum_declarations: int | None = None
    maximum_calls_per_turn: int | None = None


@dataclass(frozen=True, slots=True)
class StreamingCapability:
    supported: bool = False
    usage_during_stream: bool = False
    tool_call_deltas: bool = False


@dataclass(frozen=True, slots=True)
class InvocationCancellationCapability:
    mode: CancellationMode = CancellationMode.UNSUPPORTED
    terminal_observation: bool = True
    accounting_finality: AccountingFinality = AccountingFinality.FINAL_UNAVAILABLE
    maximum_reconciliation_time: timedelta = timedelta(0)


@dataclass(frozen=True, slots=True)
class ModelCompatibility:
    supported_input_kinds: tuple[InputKind, ...] = (InputKind.TEXT,)
    supported_response_formats: tuple[ResponseFormat, ...] = (ResponseFormat.TEXT,)
    supported_sampling_parameters: tuple[SamplingParameter, ...] = ()
    allowed_purposes: tuple[str, ...] = ()
    maximum_data_classification: DataClassification = DataClassification.INTERNAL


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    model_ref: ModelRef
    provider_ref: ProviderRef
    name: str = field(repr=False)
    provider_binding_ref: ProviderModelBindingRef = field(repr=False, default=ProviderModelBindingRef("binding:opaque"))
    context: ModelContextLimits = field(default_factory=lambda: ModelContextLimits(4096, 4096, 1024, "default"))
    cost: ModelCost = field(default_factory=ModelCost)
    vision: VisionCapability = field(default_factory=VisionCapability)
    tools: ToolCapability = field(default_factory=ToolCapability)
    streaming: StreamingCapability = field(default_factory=StreamingCapability)
    cancellation: InvocationCancellationCapability = field(default_factory=InvocationCancellationCapability)
    status: ModelStatus = ModelStatus.ACTIVE
    revision: ModelRevision = ModelRevision("model-revision:1")
    compatibility: ModelCompatibility = field(default_factory=ModelCompatibility)
    profiles: tuple[ModelProfile, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: tuple[str, ...] = field(default_factory=tuple, repr=False)
    deprecation: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _required(self.model_ref, "model_ref")
        _required(self.provider_ref, "provider_ref")
        _required(self.name, "name")
        if not isinstance(self.status, ModelStatus):
            object.__setattr__(self, "status", ModelStatus(self.status))
        if self.created_at is not None:
            _aware(self.created_at, "created_at")
        if self.updated_at is not None:
            _aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class ModelDeprecation:
    announced_at: datetime
    deprecated_at: datetime
    sunset_at: datetime | None = None
    successor_model_ref: ModelRef | None = None
    reason: str = field(repr=False, default="")
    migration_guidance: str | None = field(repr=False, default=None)

    def __post_init__(self) -> None:
        _aware(self.announced_at, "announced_at")
        _aware(self.deprecated_at, "deprecated_at")
        if self.sunset_at is not None:
            _aware(self.sunset_at, "sunset_at")


@dataclass(frozen=True, slots=True)
class ModelConstraint:
    code: ConstraintCode
    value: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class WeightedPreference:
    name: str
    weight: Decimal


@dataclass(frozen=True, slots=True)
class ModelProfileDefinition:
    profile: ModelProfile
    required: tuple[ModelConstraint, ...] = ()
    preferences: tuple[WeightedPreference, ...] = ()
    default_fallback_policy_ref: FallbackPolicyRef | None = None
    revision: ProfileRevision = ProfileRevision("profile:1")
    active: bool = True


ProfileDefinition = ModelProfileDefinition


@dataclass(frozen=True, slots=True)
class FallbackRequest:
    mode: FallbackMode = FallbackMode.DISABLED
    ordered_model_refs: tuple[ModelRef, ...] = ()
    policy_ref: FallbackPolicyRef | None = None
    allowed_failure_categories: tuple[ProviderErrorCategory, ...] = ()
    maximum_attempts: int = 1
    allow_cross_provider: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.mode, FallbackMode):
            object.__setattr__(self, "mode", FallbackMode(self.mode))
        _positive(self.maximum_attempts, "maximum_attempts")
        if self.mode is FallbackMode.EXPLICIT_ORDER and not self.ordered_model_refs:
            raise ValueError("explicit fallback requires ordered_model_refs")
        if self.mode is FallbackMode.POLICY and self.policy_ref is None:
            raise ValueError("policy fallback requires policy_ref")


@dataclass(frozen=True, slots=True)
class ModelRequirements:
    context: ModelCatalogOperationContext
    requested_profile: ModelProfile | None = None
    preferred_model_ref: ModelRef | None = None
    allowed_provider_refs: tuple[ProviderRef, ...] = ()
    denied_provider_refs: tuple[ProviderRef, ...] = ()
    allowed_model_refs: tuple[ModelRef, ...] = ()
    required_capabilities: tuple[RequiredCapability, ...] = ()
    input_kinds: tuple[InputKind, ...] = (InputKind.TEXT,)
    response_format: ResponseFormat = ResponseFormat.TEXT
    cancellation_requirement: CancellationRequirement = CancellationRequirement.ANY
    minimum_context_tokens: int = 1
    maximum_input_tokens: int = 4096
    maximum_output_tokens: int = 1024
    maximum_total_tokens: int = 5120
    maximum_cost: Decimal | None = None
    data_classification: DataClassification = DataClassification.INTERNAL
    region: str | None = None
    fallback: FallbackRequest = field(default_factory=FallbackRequest)
    catalog_version: CatalogVersion | None = None
    policy_version: ModelPolicyVersion | None = None
    cancellation: CancellationSignalRef = CancellationSignalRef("cancel:none")

    def __post_init__(self) -> None:
        if not isinstance(self.cancellation_requirement, CancellationRequirement):
            object.__setattr__(self, "cancellation_requirement", CancellationRequirement(self.cancellation_requirement))
        for name, value in (("minimum_context_tokens", self.minimum_context_tokens),
                            ("maximum_input_tokens", self.maximum_input_tokens),
                            ("maximum_output_tokens", self.maximum_output_tokens),
                            ("maximum_total_tokens", self.maximum_total_tokens)):
            _positive(value, name)
        if self.maximum_input_tokens + self.maximum_output_tokens > self.maximum_total_tokens:
            raise ValueError("input and output limits cannot exceed maximum_total_tokens")
        if self.maximum_cost is not None:
            _non_negative(self.maximum_cost, "maximum_cost")
        if self.region is not None:
            _required(self.region, "region")


@dataclass(frozen=True, slots=True)
class ResolvedCapabilities:
    required: tuple[RequiredCapability, ...] = ()
    input_kinds: tuple[InputKind, ...] = ()
    response_formats: tuple[ResponseFormat, ...] = ()
    cancellation_mode: CancellationMode = CancellationMode.UNSUPPORTED


@dataclass(frozen=True, slots=True)
class CandidateRejection:
    model_ref: ModelRef
    code: ConstraintCode
    detail: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SelectionExplanation:
    requested_profile: ModelProfile | None
    satisfied_constraints: tuple[ConstraintCode, ...] = ()
    applied_preferences: tuple[WeightedPreference, ...] = ()
    rejected_candidates: tuple[CandidateRejection, ...] = ()
    fallback_policy_ref: FallbackPolicyRef | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple, repr=False)


@dataclass(frozen=True, slots=True)
class SelectedModel:
    model_ref: ModelRef
    provider_ref: ProviderRef
    provider_binding_ref: ProviderModelBindingRef = field(repr=False)
    descriptor_revision: ModelRevision
    capabilities: ResolvedCapabilities
    estimated_cost_ceiling: Decimal | None
    rank: int
    role: ModelRole

    def __post_init__(self) -> None:
        _positive(self.rank, "rank")
        if self.estimated_cost_ceiling is not None:
            _non_negative(self.estimated_cost_ceiling, "estimated_cost_ceiling")


@dataclass(frozen=True, slots=True)
class ApprovedModelRequirementsSnapshot:
    approved_requirements_ref: ApprovedModelRequirementsRef
    selection_id: ModelSelectionId
    context: ModelCatalogOperationContext
    data_classification: DataClassification
    region: str | None
    input_kinds: tuple[InputKind, ...]
    response_format: ResponseFormat
    required_capabilities: tuple[RequiredCapability, ...]
    cancellation_requirement: CancellationRequirement
    minimum_context_tokens: int
    maximum_input_tokens: int
    maximum_output_tokens: int
    maximum_total_tokens: int
    maximum_cost: Decimal | None
    allowed_provider_refs: tuple[ProviderRef, ...]
    allowed_model_refs: tuple[ModelRef, ...]
    fallback_policy_ref: FallbackPolicyRef | None
    catalog_version: CatalogVersion
    policy_version: ModelPolicyVersion
    issued_at: datetime
    valid_until: datetime
    integrity_ref: IntegrityRef
    allowed_failure_categories: tuple[ProviderErrorCategory, ...] = ()
    maximum_fallback_attempts: int = 1
    allow_cross_provider_fallback: bool = False

    def __post_init__(self) -> None:
        _aware(self.issued_at, "issued_at")
        _aware(self.valid_until, "valid_until")
        if self.valid_until <= self.issued_at:
            raise ValueError("valid_until must be after issued_at")
        _positive(self.maximum_fallback_attempts, "maximum_fallback_attempts")

    @property
    def user_id(self):
        return self.context.user_id

    @property
    def workspace_id(self):
        return self.context.workspace_id

    @property
    def agent_id(self):
        return self.context.agent_id

    @property
    def execution_id(self):
        return self.context.execution_id

    @property
    def correlation_id(self):
        return self.context.correlation_id

    @property
    def purpose(self):
        return self.context.purpose

    @property
    def actor_ref(self):
        return self.context.actor_ref


@dataclass(frozen=True, slots=True)
class ModelSelection:
    selection_id: ModelSelectionId
    selection_ref: ModelSelectionRef
    context: ModelCatalogOperationContext
    primary: SelectedModel
    fallbacks: tuple[SelectedModel, ...]
    catalog_version: CatalogVersion
    policy_version: ModelPolicyVersion
    profile_revision: ProfileRevision | None
    pricing_revisions: tuple[PricingRevisionRef, ...]
    approved_requirements_ref: ApprovedModelRequirementsRef
    availability_snapshot_ref: AvailabilitySnapshotRef
    explanation: SelectionExplanation
    resolved_at: datetime
    valid_until: datetime

    def __post_init__(self) -> None:
        _aware(self.resolved_at, "resolved_at")
        _aware(self.valid_until, "valid_until")
        if self.valid_until <= self.resolved_at:
            raise ValueError("valid_until must be after resolved_at")


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    measurement: Measurement = Measurement.UNAVAILABLE

    def __post_init__(self) -> None:
        if not isinstance(self.measurement, Measurement):
            object.__setattr__(self, "measurement", Measurement(self.measurement))
        for name, value in (("input_tokens", self.input_tokens), ("output_tokens", self.output_tokens),
                            ("total_tokens", self.total_tokens), ("cached_input_tokens", self.cached_input_tokens),
                            ("reasoning_tokens", self.reasoning_tokens)):
            if value is not None:
                _non_negative(value, name)
        if self.total_tokens is not None and self.input_tokens is not None and self.output_tokens is not None:
            if self.total_tokens < self.input_tokens + self.output_tokens:
                raise ValueError("total_tokens cannot be lower than input plus output")

    def plus(self, other: ProviderUsage) -> ProviderUsage:
        def add(left: int | None, right: int | None) -> int | None:
            return left + right if left is not None and right is not None else None
        return ProviderUsage(add(self.input_tokens, other.input_tokens), add(self.output_tokens, other.output_tokens),
                             add(self.total_tokens, other.total_tokens), add(self.cached_input_tokens, other.cached_input_tokens),
                             add(self.reasoning_tokens, other.reasoning_tokens),
                             Measurement.CONFIRMED if self.measurement is Measurement.CONFIRMED and other.measurement is Measurement.CONFIRMED else Measurement.UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class ProviderCost:
    amount: Decimal | None = None
    currency: str | None = None
    measurement: Measurement = Measurement.UNAVAILABLE
    pricing_revision: PricingRevisionRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.measurement, Measurement):
            object.__setattr__(self, "measurement", Measurement(self.measurement))
        if self.amount is not None:
            _non_negative(self.amount, "amount")
        if self.currency is not None:
            _required(self.currency, "currency")

    def plus(self, other: ProviderCost) -> ProviderCost:
        amount = self.amount + other.amount if self.amount is not None and other.amount is not None else None
        return ProviderCost(amount, self.currency or other.currency, Measurement.CONFIRMED if self.measurement is Measurement.CONFIRMED and other.measurement is Measurement.CONFIRMED else Measurement.UNAVAILABLE, other.pricing_revision or self.pricing_revision)


@dataclass(frozen=True, slots=True)
class ProviderError:
    category: ProviderErrorCategory
    code: PublicErrorCode | str
    message: str = field(repr=False)
    retryability: Retryability = Retryability.NEVER
    retry_after: timedelta | None = None
    request_accepted: RequestAcceptance = RequestAcceptance.UNKNOWN
    partial_output_available: bool = False
    provider_ref: ProviderRef = ProviderRef("provider:unknown")
    cause_ref: InternalDiagnosticRef | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.category, ProviderErrorCategory):
            object.__setattr__(self, "category", ProviderErrorCategory(self.category))
        if not isinstance(self.retryability, Retryability):
            object.__setattr__(self, "retryability", Retryability(self.retryability))
        _required(self.code, "code")
        _required(self.message, "message")
        normalized = self.message.lower().replace("-", "_")
        if any(token in normalized for token in ("api_key", "authorization:", "password=", "secret=", "credential=")):
            raise ValueError("message must be sanitized")


class ContentRole(StrEnum):
    SYSTEM = "SYSTEM"
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    TOOL = "TOOL"


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        _required(self.text, "text")


@dataclass(frozen=True, slots=True)
class ImagePart:
    image_ref: ImageReference
    media_type: str
    detail: str = "AUTO"


@dataclass(frozen=True, slots=True)
class ToolResultPart:
    tool_call_id: ToolCallId
    result_ref: ResultReference
    status: str


@dataclass(frozen=True, slots=True)
class RefusalPart:
    category: str
    summary: str = field(repr=False)


ContentPart = TextPart | ImagePart | ToolResultPart | RefusalPart


@dataclass(frozen=True, slots=True)
class ProviderMessage:
    role: ContentRole
    parts: tuple[ContentPart, ...]
    tool_call_id: ToolCallId | None = None


@dataclass(frozen=True, slots=True)
class ToolDeclaration:
    tool_ref: ToolRef
    name: str
    description: str = field(repr=False)
    input_schema: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SamplingPolicy:
    temperature: Decimal | None = None
    top_p: Decimal | None = None
    stop: tuple[str, ...] = ()
    seed: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderInvocationLimits:
    maximum_input_tokens: int
    maximum_output_tokens: int
    maximum_total_tokens: int
    maximum_cost: Decimal | None = None
    timeout: timedelta = timedelta(seconds=60)
    first_chunk_timeout: timedelta | None = None
    idle_stream_timeout: timedelta | None = None
    maximum_tool_calls: int = 0
    maximum_images: int = 0
    maximum_image_bytes: int | None = None

    def __post_init__(self) -> None:
        for name, value in (("maximum_input_tokens", self.maximum_input_tokens), ("maximum_output_tokens", self.maximum_output_tokens), ("maximum_total_tokens", self.maximum_total_tokens)):
            _positive(value, name)
        for name, value in (("maximum_tool_calls", self.maximum_tool_calls), ("maximum_images", self.maximum_images), ("maximum_image_bytes", self.maximum_image_bytes)):
            if value is not None:
                _non_negative(value, name)
        if self.maximum_cost is not None:
            _non_negative(self.maximum_cost, "maximum_cost")


@dataclass(frozen=True, slots=True)
class ProviderInvocationRequest:
    invocation_id: ProviderInvocationId
    context: ProviderOperationContext
    selection: ModelSelection
    approved_requirements_ref: ApprovedModelRequirementsRef
    approved_requirements: ApprovedModelRequirementsSnapshot
    messages: tuple[ProviderMessage, ...] = ()
    tools: tuple[ToolDeclaration, ...] = ()
    response_format: ResponseFormat = ResponseFormat.TEXT
    sampling: SamplingPolicy = field(default_factory=SamplingPolicy)
    limits: ProviderInvocationLimits = field(default_factory=lambda: ProviderInvocationLimits(4096, 1024, 5120))
    cancellation: CancellationSignalRef = CancellationSignalRef("cancel:none")
    idempotency_key: IdempotencyKey | str | None = None


@dataclass(frozen=True, slots=True)
class ModelMessage:
    parts: tuple[ContentPart, ...]
    role: ContentRole = ContentRole.ASSISTANT


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    tool_call_id: ToolCallId
    tool_name: str
    arguments: Any = field(repr=False)


@dataclass(frozen=True, slots=True)
class GenerationSucceeded:
    invocation_id: ProviderInvocationId
    message: ModelMessage
    usage: ProviderUsage
    cost: ProviderCost
    finish_reason: FinishReason = FinishReason.STOP


@dataclass(frozen=True, slots=True)
class ToolCallsRequested:
    invocation_id: ProviderInvocationId
    calls: tuple[ToolCallRequest, ...]
    assistant_content: tuple[ContentPart, ...]
    usage: ProviderUsage
    cost: ProviderCost
    finish_reason: FinishReason = FinishReason.TOOL_CALLS


@dataclass(frozen=True, slots=True)
class UserInputRequested:
    invocation_id: ProviderInvocationId
    input_request_ref: OpaqueRef
    usage: ProviderUsage
    cost: ProviderCost


@dataclass(frozen=True, slots=True)
class GenerationFailed:
    invocation_id: ProviderInvocationId
    error: ProviderError
    usage: ProviderUsage
    cost: ProviderCost


@dataclass(frozen=True, slots=True)
class GenerationCancelled:
    invocation_id: ProviderInvocationId
    reason: CancellationReason
    usage: ProviderUsage
    cost: ProviderCost


@dataclass(frozen=True, slots=True)
class GenerationIndeterminate:
    invocation_id: ProviderInvocationId
    error: ProviderError
    usage: ProviderUsage
    cost: ProviderCost


ProviderOutcome = GenerationSucceeded | ToolCallsRequested | UserInputRequested | GenerationFailed | GenerationCancelled | GenerationIndeterminate
ProviderInvocationOutcome = ProviderOutcome
ProviderRequest = ProviderInvocationRequest
ProviderFinalResult = GenerationSucceeded


@dataclass(frozen=True, slots=True)
class ProviderStream:
    stream_id: ProviderStreamId
    invocation_id: ProviderInvocationId
    opened_at: datetime

    def __post_init__(self) -> None:
        _aware(self.opened_at, "opened_at")


@dataclass(frozen=True, slots=True)
class StreamOpened:
    stream_id: ProviderStreamId
    sequence: int

    def __post_init__(self) -> None:
        _positive(self.sequence, "sequence")


@dataclass(frozen=True, slots=True)
class ContentDelta:
    stream_id: ProviderStreamId
    sequence: int
    delta: str = field(repr=False)

    def __post_init__(self) -> None:
        _positive(self.sequence, "sequence")


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    stream_id: ProviderStreamId
    sequence: int
    tool_call_id: ToolCallId
    delta: object = field(repr=False)

    def __post_init__(self) -> None:
        _positive(self.sequence, "sequence")


@dataclass(frozen=True, slots=True)
class UsageUpdated:
    stream_id: ProviderStreamId
    sequence: int
    usage: ProviderUsage
    cost: ProviderCost

    def __post_init__(self) -> None:
        _positive(self.sequence, "sequence")


@dataclass(frozen=True, slots=True)
class StreamCompleted:
    stream_id: ProviderStreamId
    sequence: int
    outcome: ProviderOutcome

    def __post_init__(self) -> None:
        _positive(self.sequence, "sequence")


@dataclass(frozen=True, slots=True)
class StreamFailed:
    stream_id: ProviderStreamId
    sequence: int
    error: ProviderError
    usage: ProviderUsage
    cost: ProviderCost

    def __post_init__(self) -> None:
        _positive(self.sequence, "sequence")


@dataclass(frozen=True, slots=True)
class StreamCancelled:
    stream_id: ProviderStreamId
    sequence: int
    reason: CancellationReason
    usage: ProviderUsage
    cost: ProviderCost

    def __post_init__(self) -> None:
        _positive(self.sequence, "sequence")


ProviderStreamEvent = StreamOpened | ContentDelta | ToolCallDelta | UsageUpdated | StreamCompleted | StreamFailed | StreamCancelled


@dataclass(frozen=True, slots=True)
class ProviderTerminalSnapshot:
    terminal_ref: ProviderTerminalRef
    invocation_id: ProviderInvocationId
    stream_id: ProviderStreamId | None
    state: ProviderTerminalState
    outcome: ProviderOutcome
    usage: ProviderUsage
    cost: ProviderCost
    accounting_finality: AccountingFinality
    finalized_at: datetime
    retained_until: datetime


@dataclass(frozen=True, slots=True)
class ProviderInvocationSnapshot:
    invocation_id: ProviderInvocationId
    context: ProviderOperationContext
    selection_ref: ModelSelectionRef
    state: ProviderTerminalState | str
    outcome: ProviderOutcome | None
    usage: ProviderUsage
    cost: ProviderCost


@dataclass(frozen=True, slots=True)
class ReadProviderStream:
    context: ProviderOperationContext
    stream_id: ProviderStreamId
    after_sequence: int
    maximum_events: int
    wait_timeout: timedelta


@dataclass(frozen=True, slots=True)
class CancelProviderInvocation:
    request_id: ProviderCancelRequestId
    context: ProviderOperationContext
    invocation_id: ProviderInvocationId
    reason: CancellationReason
    idempotency_key: IdempotencyKey | str


@dataclass(frozen=True, slots=True)
class AwaitProviderTerminal:
    context: ProviderOperationContext
    invocation_id: ProviderInvocationId
    terminal_ref: ProviderTerminalRef
    wait_timeout: timedelta


@dataclass(frozen=True, slots=True)
class AuthorizedProviderInvocationQuery:
    context: ProviderOperationContext
    invocation_id: ProviderInvocationId


@dataclass(frozen=True, slots=True)
class CancelAccepted:
    acknowledged_at: datetime
    terminal_ref: ProviderTerminalRef


@dataclass(frozen=True, slots=True)
class AlreadyCancelled:
    acknowledged_at: datetime
    terminal_ref: ProviderTerminalRef


@dataclass(frozen=True, slots=True)
class AlreadyTerminal:
    terminal: ProviderTerminalState
    terminal_ref: ProviderTerminalRef


@dataclass(frozen=True, slots=True)
class CancelRejected:
    reason: str


CancelProviderResult = CancelAccepted | AlreadyCancelled | AlreadyTerminal | CancelRejected


@dataclass(frozen=True, slots=True)
class ModelResolutionRequest:
    request_id: OpaqueRef
    requirements: ModelRequirements
    idempotency_key: IdempotencyKey | str


ResolveModel = ModelResolutionRequest


@dataclass(frozen=True, slots=True)
class ResolveFallback:
    request_id: OpaqueRef
    context: ModelCatalogOperationContext
    prior_selection_ref: ModelSelectionRef
    failed_model_ref: ModelRef
    failure_category: ProviderErrorCategory
    consumed_usage: ProviderUsage
    consumed_cost: ProviderCost
    remaining_limits: ProviderInvocationLimits
    cancellation: CancellationSignalRef
    idempotency_key: IdempotencyKey | str


@dataclass(frozen=True, slots=True)
class ModelResolved:
    selection: ModelSelection


@dataclass(frozen=True, slots=True)
class NoCompatibleModel:
    error: str
    considered: tuple[CandidateRejection, ...]


@dataclass(frozen=True, slots=True)
class ModelResolutionCancelled:
    reason: CancellationReason


ModelResolutionOutcome = ModelResolved | NoCompatibleModel | ModelResolutionCancelled


class ProviderContractError(RuntimeError):
    def __init__(self, category: ProviderErrorCategory, code: str = "PROVIDER_CONTRACT_REJECTED") -> None:
        self.category = category
        self.code = code
        super().__init__(code)


class CatalogConflictError(RuntimeError):
    ...


@dataclass(frozen=True, slots=True)
class CatalogMutationRequest:
    request_id: OpaqueRef
    context: ModelCatalogOperationContext
    expected_catalog_version: int
    idempotency_key: IdempotencyKey | str

    def __post_init__(self) -> None:
        _non_negative(self.expected_catalog_version, "expected_catalog_version")
        _required(self.request_id, "request_id")
        _required(self.idempotency_key, "idempotency_key")


@dataclass(frozen=True, slots=True)
class RegisterProvider(CatalogMutationRequest):
    descriptor: ProviderDescriptor


@dataclass(frozen=True, slots=True)
class RegisterModel(CatalogMutationRequest):
    descriptor: ModelDescriptor


@dataclass(frozen=True, slots=True)
class PublishModelProfile(CatalogMutationRequest):
    profile: ModelProfileDefinition


@dataclass(frozen=True, slots=True)
class PublishModelPricing(CatalogMutationRequest):
    model_ref: ModelRef
    cost: ModelCost


@dataclass(frozen=True, slots=True)
class ChangeModelStatus(CatalogMutationRequest):
    model_ref: ModelRef
    expected_status: ModelStatus
    new_status: ModelStatus
    reason: str = field(repr=False)
    new_revision: ModelRevision | None = None


@dataclass(frozen=True, slots=True)
class ChangeProviderStatus(CatalogMutationRequest):
    provider_ref: ProviderRef
    expected_status: ProviderStatus
    new_status: ProviderStatus
    reason: str = field(repr=False)
    new_revision: ProviderRevision | None = None


@dataclass(frozen=True, slots=True)
class CatalogMutationResult:
    catalog_version: int
    reference: OpaqueRef
    status: str


@dataclass(frozen=True, slots=True)
class AuthorizedModelQuery:
    context: ModelCatalogOperationContext
    model_ref: ModelRef
    revision: ModelRevision | None = None


@dataclass(frozen=True, slots=True)
class AuthorizedModelListQuery:
    context: ModelCatalogOperationContext
    profiles: tuple[ModelProfile, ...] = ()
    providers: tuple[ProviderRef, ...] = ()
    statuses: tuple[ModelStatus, ...] = ()
    required_capabilities: tuple[RequiredCapability, ...] = ()


@dataclass(frozen=True, slots=True)
class AuthorizedModelSelectionQuery:
    context: ModelCatalogOperationContext
    selection_ref: ModelSelectionRef


@dataclass(frozen=True, slots=True)
class ModelPage:
    items: tuple[ModelDescriptor, ...]
    catalog_version: int


@dataclass(frozen=True, slots=True)
class CatalogSelectionRecord:
    selection: ModelSelection
    snapshot: ApprovedModelRequirementsSnapshot


__all__ = [name for name, value in list(globals().items()) if not name.startswith("_") and (isinstance(value, type) or name in {"ContentPart", "ProviderOutcome", "ProviderStreamEvent", "ModelResolutionOutcome"})]
