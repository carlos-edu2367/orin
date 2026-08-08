from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from threading import Event
from typing import Any, Mapping, Protocol

from agentos.events.models import DataClassification
from agentos.resources.models import EffectState, ResourceBudget, ResourceCapability, ResourceType, Retryability


MAX_TEXT = 256
MAX_SCHEMA_DEPTH = 12


def _text(value: object, field_name: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be a bounded non-blank string")
    return value


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class ToolStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    DISABLED = "DISABLED"


class ToolIdempotency(StrEnum):
    IDEMPOTENT = "IDEMPOTENT"
    IDEMPOTENT_WITH_KEY = "IDEMPOTENT_WITH_KEY"
    NON_IDEMPOTENT = "NON_IDEMPOTENT"


IdempotencyPolicy = ToolIdempotency


class ToolInvocationState(StrEnum):
    REQUESTED = "REQUESTED"
    VALIDATED = "VALIDATED"
    AUTHORIZED = "AUTHORIZED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


class ToolErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    DISABLED = "DISABLED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    EXECUTION_FAILED = "EXECUTION_FAILED"


class StreamDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    BACKPRESSURE = "BACKPRESSURE"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class SensitiveOperationContext:
    user_id: str
    workspace_id: str | None
    agent_id: str
    execution_id: str
    correlation_id: str
    purpose: str
    actor: str

    def __post_init__(self) -> None:
        for name in ("user_id", "agent_id", "execution_id", "correlation_id", "purpose", "actor"):
            _text(getattr(self, name), name)
        if self.workspace_id is not None:
            _text(self.workspace_id, "workspace_id")

    def scope_key(self) -> tuple[str, ...]:
        return (self.user_id, self.workspace_id or "", self.agent_id, self.execution_id, self.correlation_id, self.purpose, self.actor)

    def __repr__(self) -> str:
        return f"SensitiveOperationContext(user_id={self.user_id!r}, workspace_id={self.workspace_id!r}, agent_id={self.agent_id!r}, execution_id={self.execution_id!r}, correlation_id={self.correlation_id!r}, purpose=<bounded>, actor={self.actor!r})"


@dataclass(frozen=True, slots=True)
class ToolRegistryOperationContext:
    user_id: str
    workspace_id: str | None
    agent_id: str | None
    execution_id: str | None
    correlation_id: str
    administrative_correlation_id: str | None
    purpose: str
    actor: str

    def __post_init__(self) -> None:
        _text(self.user_id, "user_id"); _text(self.correlation_id, "correlation_id"); _text(self.purpose, "purpose"); _text(self.actor, "actor")
        if (self.execution_id is None) == (self.administrative_correlation_id is None):
            raise ValueError("exactly one execution or administrative correlation is required")
        if self.execution_id is not None and self.agent_id is None:
            raise ValueError("agent_id is required for execution-bound registry operation")


@dataclass(frozen=True, slots=True)
class ToolRef:
    tool_id: str
    version: int

    def __post_init__(self) -> None:
        _text(self.tool_id, "tool_id")
        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError("tool version must be positive")


@dataclass(frozen=True, slots=True)
class ResourceRequirement:
    resource_type: ResourceType
    capabilities: tuple[ResourceCapability, ...]
    budget: ResourceBudget = ResourceBudget()

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_type", ResourceType(self.resource_type))
        capabilities = tuple(ResourceCapability(item) for item in self.capabilities)
        if not capabilities or len(set(capabilities)) != len(capabilities):
            raise ValueError("resource requirement capabilities are invalid")
        object.__setattr__(self, "capabilities", capabilities)


@dataclass(frozen=True, slots=True)
class ToolLimits:
    timeout: timedelta
    maximum_input_bytes: int
    maximum_output_bytes: int
    maximum_stream_items: int
    maximum_resource_usage: ResourceBudget = ResourceBudget()

    def __post_init__(self) -> None:
        if self.timeout <= timedelta(0) or self.timeout > timedelta(hours=1):
            raise ValueError("tool timeout is invalid")
        if any(not isinstance(item, int) or item < 0 for item in (self.maximum_input_bytes, self.maximum_output_bytes, self.maximum_stream_items)):
            raise ValueError("tool limits must be non-negative")


def validate_closed_schema(schema: Mapping[str, Any]) -> None:
    """Accept a deliberately small JSON-schema subset and reject open objects."""
    def visit(node: object, depth: int) -> None:
        if not isinstance(node, Mapping) or depth > MAX_SCHEMA_DEPTH:
            raise ValueError("schema is invalid")
        kind = node.get("type")
        if kind not in {"object", "array", "string", "integer", "number", "boolean", "null"}:
            raise ValueError("schema type is invalid")
        if kind == "object":
            if node.get("additionalProperties") is not False:
                raise ValueError("object schemas must be closed")
            props = node.get("properties", {})
            if not isinstance(props, Mapping):
                raise ValueError("schema properties are invalid")
            required = node.get("required", ())
            if not isinstance(required, (list, tuple)) or not set(required).issubset(props):
                raise ValueError("schema required fields are invalid")
            for value in props.values(): visit(value, depth + 1)
        elif kind == "array":
            visit(node.get("items"), depth + 1)
    visit(schema, 0)


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    tool_ref: ToolRef
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    permissions: tuple[str, ...]
    resource_requirements: tuple[ResourceRequirement, ...]
    limits: ToolLimits
    supports_streaming: bool
    supports_cancellation: bool
    idempotency: ToolIdempotency
    result_classification: DataClassification
    status: ToolStatus = ToolStatus.ACTIVE

    def __post_init__(self) -> None:
        _text(self.name, "name"); _text(self.description, "description")
        validate_closed_schema(self.input_schema); validate_closed_schema(self.output_schema)
        permissions = tuple(_text(item, "permission") for item in self.permissions)
        if len(set(permissions)) != len(permissions): raise ValueError("permissions must be unique")
        object.__setattr__(self, "permissions", permissions)
        object.__setattr__(self, "resource_requirements", tuple(self.resource_requirements))
        object.__setattr__(self, "idempotency", ToolIdempotency(self.idempotency))
        object.__setattr__(self, "result_classification", DataClassification(self.result_classification))
        object.__setattr__(self, "status", ToolStatus(self.status))


@dataclass(frozen=True, slots=True)
class ToolLimitRequest:
    timeout: timedelta | None = None
    maximum_output_bytes: int | None = None
    maximum_stream_items: int | None = None


@dataclass(frozen=True, slots=True)
class ToolInvocationRequest:
    invocation_id: str
    tool_ref: ToolRef
    context: SensitiveOperationContext
    arguments: Mapping[str, Any]
    idempotency_key: str | None = None
    requested_limits: ToolLimitRequest = ToolLimitRequest()

    def __post_init__(self) -> None:
        _text(self.invocation_id, "invocation_id")
        if self.idempotency_key is not None: _text(self.idempotency_key, "idempotency_key")
        if not isinstance(self.arguments, Mapping): raise ValueError("arguments must be a mapping")


@dataclass(frozen=True, slots=True)
class RegisterTool:
    request_id: str
    context: ToolRegistryOperationContext
    descriptor: ToolDescriptor
    factory: object
    package_integrity_ref: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DisableTool:
    request_id: str
    context: ToolRegistryOperationContext
    tool_ref: ToolRef
    expected_status: ToolStatus
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    descriptor: ToolDescriptor
    change: str


@dataclass(frozen=True, slots=True)
class CancelToolInvocation:
    invocation_id: str
    context: SensitiveOperationContext
    reason: str = "cancel requested"


@dataclass(frozen=True, slots=True)
class AuthorizedToolInvocationQuery:
    invocation_id: str
    context: SensitiveOperationContext


@dataclass(frozen=True, slots=True)
class ToolError:
    code: ToolErrorCode | str
    retryability: Retryability = Retryability.NEVER
    effect_state: EffectState = EffectState.NOT_APPLIED
    reason: str = "tool invocation failed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", ToolErrorCode(self.code))
        object.__setattr__(self, "retryability", Retryability(self.retryability))
        object.__setattr__(self, "effect_state", EffectState(self.effect_state))
        _text(self.reason, "reason", 128)


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    operations: int = 0
    bytes_used: int = 0
    duration: timedelta = timedelta(0)


@dataclass(frozen=True, slots=True)
class ToolSucceeded:
    invocation_id: str
    result: Mapping[str, Any] | None
    usage: ResourceUsage = ResourceUsage()
    result_ref: str | None = None
    effect_state: EffectState = EffectState.APPLIED


@dataclass(frozen=True, slots=True)
class ToolFailed:
    invocation_id: str
    error: ToolError
    usage: ResourceUsage = ResourceUsage()
    result_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCancelled:
    invocation_id: str
    reason: str
    partial_result_ref: str | None = None
    usage: ResourceUsage = ResourceUsage()


ToolInvocationOutcome = ToolSucceeded | ToolFailed | ToolCancelled


@dataclass(frozen=True, slots=True)
class ToolInvocationSnapshot:
    invocation_id: str
    tool_ref: ToolRef
    context: SensitiveOperationContext
    state: ToolInvocationState
    outcome: ToolInvocationOutcome | None
    resource_lease_refs: tuple[str, ...] = ()
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ToolStreamItem:
    invocation_id: str
    sequence: int
    occurred_at: datetime
    kind: str
    value: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.sequence < 1: raise ValueError("stream sequence must be positive")
        _aware(self.occurred_at, "occurred_at")
        if self.kind not in {"PROGRESS", "DATA", "WARNING"}: raise ValueError("stream kind is invalid")


class ToolStreamSink(Protocol):
    def emit(self, item: ToolStreamItem) -> StreamDisposition: ...
    def close(self, terminal: ToolInvocationOutcome) -> None: ...


@dataclass(frozen=True, slots=True)
class CancellationSignal:
    _event: Event = field(default_factory=Event, compare=False, repr=False)
    def requested(self) -> bool: return self._event.is_set()
    def _cancel(self) -> None: self._event.set()


@dataclass(frozen=True, slots=True)
class AtomicToolCall:
    invocation_id: str
    context: SensitiveOperationContext
    input: Mapping[str, Any]
    resources: tuple[object, ...]
    cancellation: CancellationSignal
    deadline: datetime


class AtomicTool(Protocol):
    def execute(self, call: AtomicToolCall) -> Mapping[str, Any] | ToolInvocationOutcome: ...


@dataclass(frozen=True, slots=True)
class ToolOutboxEntry:
    event_type: str
    invocation_id: str
    tool_ref: ToolRef
    correlation_id: str
    payload: Mapping[str, Any]
    occurred_at: datetime
    context: SensitiveOperationContext


__all__ = [name for name in globals() if not name.startswith("_")]
