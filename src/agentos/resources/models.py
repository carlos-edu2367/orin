from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import hashlib
from typing import Mapping


MAX_TEXT = 256
MAX_CAPABILITIES = 32


def _required(value: object, name: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds its maximum length")
    return value


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class ResourceType(StrEnum):
    FILESYSTEM = "FILESYSTEM"
    TERMINAL = "TERMINAL"
    BROWSER = "BROWSER"


class IsolationMode(StrEnum):
    PROCESS = "PROCESS"
    WORKSPACE = "WORKSPACE"
    USER = "USER"
    SESSION = "SESSION"
    HOST = "HOST"


class ResourceHealth(StrEnum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    QUARANTINED = "QUARANTINED"


class ResourceLeaseState(StrEnum):
    REQUESTED = "REQUESTED"
    LEASED = "LEASED"
    REVOKING = "REVOKING"
    RELEASED = "RELEASED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class ResourceErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"
    HEALTH_REJECTED = "HEALTH_REJECTED"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_RELEASED = "LEASE_RELEASED"
    LEASE_REVOKED = "LEASE_REVOKED"
    FENCE_REJECTED = "FENCE_REJECTED"
    HANDLE_INVALID = "HANDLE_INVALID"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    ALLOCATION_UNKNOWN = "ALLOCATION_UNKNOWN"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"


class EffectState(StrEnum):
    NOT_APPLIED = "NOT_APPLIED"
    APPLIED = "APPLIED"
    UNKNOWN = "UNKNOWN"


class Retryability(StrEnum):
    NEVER = "NEVER"
    SAFE = "SAFE"
    AFTER_RECONCILIATION = "AFTER_RECONCILIATION"


class ResourceCapability(StrEnum):
    FILESYSTEM_STAT = "FILESYSTEM_STAT"
    FILESYSTEM_LIST = "FILESYSTEM_LIST"
    FILESYSTEM_READ = "FILESYSTEM_READ"
    FILESYSTEM_CREATE_DIRECTORY = "FILESYSTEM_CREATE_DIRECTORY"
    FILESYSTEM_WRITE = "FILESYSTEM_WRITE"
    FILESYSTEM_MOVE = "FILESYSTEM_MOVE"
    FILESYSTEM_COPY = "FILESYSTEM_COPY"
    FILESYSTEM_REMOVE = "FILESYSTEM_REMOVE"
    TERMINAL_SESSION = "TERMINAL_SESSION"
    TERMINAL_CANCEL = "TERMINAL_CANCEL"
    BROWSER_SESSION = "BROWSER_SESSION"
    BROWSER_NAVIGATE = "BROWSER_NAVIGATE"
    BROWSER_CANCEL = "BROWSER_CANCEL"
    INSPECT = "INSPECT"


@dataclass(frozen=True, slots=True)
class ResourceOperationContext:
    user_id: str
    workspace_id: str | None
    agent_id: str
    execution_id: str
    correlation_id: str
    purpose: str
    actor: str

    def __post_init__(self) -> None:
        for name in ("user_id", "agent_id", "execution_id", "correlation_id", "actor"):
            _required(getattr(self, name), name)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
            if any(marker in self.workspace_id for marker in ("/", "\\", ":", "..")):
                raise ValueError("workspace_id must be opaque")
        _required(self.purpose, "purpose")

    def scope_key(self) -> tuple[str, ...]:
        return (self.user_id, self.workspace_id or "", self.agent_id, self.execution_id, self.correlation_id, self.purpose, self.actor)

    def binding_key(self) -> tuple[str, ...]:
        return (self.user_id, self.workspace_id or "", self.agent_id, self.execution_id, self.purpose, self.actor)

    def __repr__(self) -> str:
        return f"ResourceOperationContext(user_id={self.user_id!r}, workspace_id={self.workspace_id!r}, agent_id={self.agent_id!r}, execution_id={self.execution_id!r}, correlation_id={self.correlation_id!r}, purpose=<bounded>, actor={self.actor!r})"


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    maximum_duration: timedelta = timedelta(minutes=5)
    maximum_operations: int = 100
    maximum_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if self.maximum_duration <= timedelta(0) or self.maximum_duration > timedelta(hours=1):
            raise ValueError("resource duration is invalid")
        if any(not isinstance(value, int) or value < 0 for value in (self.maximum_operations, self.maximum_bytes)):
            raise ValueError("resource limits are invalid")


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    maximum_operations: int = 100
    maximum_bytes: int = 1024 * 1024
    maximum_duration: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if any(not isinstance(value, int) or value < 0 for value in (self.maximum_operations, self.maximum_bytes)):
            raise ValueError("resource budget is invalid")
        if self.maximum_duration <= timedelta(0) or self.maximum_duration > timedelta(hours=1):
            raise ValueError("resource budget duration is invalid")


@dataclass(frozen=True, slots=True)
class ResourceDescriptor:
    resource_type: ResourceType
    adapter_ref: str
    capabilities: tuple[ResourceCapability, ...]
    isolation_modes: tuple[IsolationMode, ...]
    limits: ResourceLimits
    health: ResourceHealth = ResourceHealth.AVAILABLE

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_type", ResourceType(self.resource_type))
        object.__setattr__(self, "health", ResourceHealth(self.health))
        object.__setattr__(self, "capabilities", tuple(ResourceCapability(cap) for cap in self.capabilities))
        object.__setattr__(self, "isolation_modes", tuple(IsolationMode(mode) for mode in self.isolation_modes))
        _required(self.adapter_ref, "adapter_ref")
        if not self.capabilities or len(self.capabilities) > MAX_CAPABILITIES or len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("resource capabilities are invalid")
        if not self.isolation_modes:
            raise ValueError("resource isolation modes cannot be empty")


@dataclass(frozen=True, slots=True)
class AuthorizedResourceHandle:
    handle_ref: str
    lease_id: str
    operation_id: str
    capabilities: tuple[ResourceCapability, ...]
    expires_at: datetime

    def __post_init__(self) -> None:
        for name in ("handle_ref", "lease_id", "operation_id"):
            _required(getattr(self, name), name)
        object.__setattr__(self, "capabilities", tuple(ResourceCapability(cap) for cap in self.capabilities))
        _aware(self.expires_at, "expires_at")

    def __repr__(self) -> str:
        return "AuthorizedResourceHandle(<ephemeral>)"

    def __reduce_ex__(self, protocol: int):
        raise TypeError("resource handles are ephemeral and not serializable")


@dataclass(frozen=True, slots=True)
class ResourceLease:
    lease_id: str
    resource_ref: str
    resource_type: ResourceType
    context: ResourceOperationContext
    permissions: tuple[ResourceCapability, ...]
    isolation_key: str
    budget: ResourceBudget
    state: ResourceLeaseState
    acquired_at: datetime
    expires_at: datetime
    fencing_token: int
    adapter_ref: str
    workspace_lease_id: str | None = None
    released_at: datetime | None = None
    cleanup_confirmed: bool = False
    usage_operations: int = 0
    usage_bytes: int = 0

    def __post_init__(self) -> None:
        for name in ("lease_id", "resource_ref", "isolation_key", "adapter_ref"):
            _required(getattr(self, name), name)
        object.__setattr__(self, "resource_type", ResourceType(self.resource_type))
        object.__setattr__(self, "state", ResourceLeaseState(self.state))
        object.__setattr__(self, "permissions", tuple(ResourceCapability(cap) for cap in self.permissions))
        if self.fencing_token < 1 or self.expires_at <= self.acquired_at:
            raise ValueError("resource lease timing/fence is invalid")
        _aware(self.acquired_at, "acquired_at")
        _aware(self.expires_at, "expires_at")


@dataclass(frozen=True, slots=True)
class ResourceLeaseRequest:
    request_id: str
    context: ResourceOperationContext
    resource_type: ResourceType
    required_capabilities: tuple[ResourceCapability, ...]
    requested_permissions: tuple[ResourceCapability, ...]
    requested_budget: ResourceBudget
    requested_duration: timedelta
    idempotency_key: str

    def __post_init__(self) -> None:
        _required(self.request_id, "request_id")
        _required(self.idempotency_key, "idempotency_key")
        object.__setattr__(self, "resource_type", ResourceType(self.resource_type))
        object.__setattr__(self, "required_capabilities", tuple(ResourceCapability(cap) for cap in self.required_capabilities))
        object.__setattr__(self, "requested_permissions", tuple(ResourceCapability(cap) for cap in self.requested_permissions))
        if self.requested_duration <= timedelta(0) or self.requested_duration > timedelta(hours=1):
            raise ValueError("requested resource duration is invalid")


@dataclass(frozen=True, slots=True)
class RenewResourceLease:
    request_id: str
    lease_id: str
    context: ResourceOperationContext
    requested_extension: timedelta
    expected_expires_at: datetime
    fencing_token: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class AuthorizeResourceOperation:
    lease_id: str
    operation_id: str
    context: ResourceOperationContext
    capability: ResourceCapability
    requested_usage_operations: int = 1
    requested_usage_bytes: int = 0


@dataclass(frozen=True, slots=True)
class ReleaseResourceLease:
    request_id: str
    lease_id: str
    context: ResourceOperationContext
    fencing_token: int
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RevokeResourceLease:
    request_id: str
    lease_id: str
    context: ResourceOperationContext
    fencing_token: int
    reason: str
    cleanup_deadline: datetime
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ResourceError:
    code: ResourceErrorCode
    reason: str = "resource operation failed"
    retryability: Retryability = Retryability.NEVER
    effect_state: EffectState = EffectState.NOT_APPLIED

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", ResourceErrorCode(self.code))
        object.__setattr__(self, "retryability", Retryability(self.retryability))
        object.__setattr__(self, "effect_state", EffectState(self.effect_state))
        _required(self.reason, "reason", 128)

    def __repr__(self) -> str:
        return f"ResourceError(code={self.code.value!r}, retryability={self.retryability.value!r}, effect_state={self.effect_state.value!r})"


@dataclass(frozen=True, slots=True)
class ResourceUsageRecord:
    lease_id: str
    execution_id: str
    operation_id: str
    resource_type: ResourceType
    purpose: str
    started_at: datetime
    finished_at: datetime | None
    usage_operations: int
    usage_bytes: int
    outcome: str | None


@dataclass(frozen=True, slots=True)
class CleanupResult:
    lease_id: str
    state: str
    effect_state: EffectState
    checkpoint: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    resource_ref: str
    inspected: int
    repaired: int
    effect_state: EffectState
    checkpoint: str | None = None
    evidence_codes: tuple[str, ...] = ()


def isolation_fingerprint(context: ResourceOperationContext, descriptor: ResourceDescriptor) -> str:
    value = "|".join((context.user_id, context.workspace_id or "", context.agent_id, context.execution_id, descriptor.resource_type.value, descriptor.adapter_ref))
    return hashlib.sha256(value.encode()).hexdigest()[:32]


__all__ = [name for name in globals() if not name.startswith("_")]
