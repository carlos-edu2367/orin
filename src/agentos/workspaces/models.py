from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Mapping

from agentos.events.models import DataClassification


MAX_ID = 255
MAX_TEXT = 256
MAX_REASON = 128
MAX_PERMISSIONS = 16
MAX_MANIFEST_CATEGORIES = 32


def _required(value: object, name: str, maximum: int = MAX_ID) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds its maximum length")
    return value


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class WorkspaceState(StrEnum):
    PROVISIONING = "PROVISIONING"
    ACTIVE = "ACTIVE"
    SUSPENDING = "SUSPENDING"
    SUSPENDED = "SUSPENDED"
    ARCHIVING = "ARCHIVING"
    ARCHIVED = "ARCHIVED"
    DELETING = "DELETING"
    DELETED = "DELETED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    FAILED = "FAILED"


class WorkspacePermission(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    LIST = "LIST"
    ADMIN = "ADMIN"
    DELETE = "DELETE"
    RECONCILE = "RECONCILE"


class LeaseState(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKING = "REVOKING"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class LockState(StrEnum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class RootHealth(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    QUARANTINED = "QUARANTINED"
    MISSING = "MISSING"


class UsageReconciliationState(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    IN_PROGRESS = "IN_PROGRESS"
    DIVERGENT = "DIVERGENT"


class EffectState(StrEnum):
    NOT_APPLIED = "NOT_APPLIED"
    APPLIED = "APPLIED"
    UNKNOWN = "UNKNOWN"


class Retryability(StrEnum):
    NEVER = "NEVER"
    SAFE = "SAFE"
    AFTER_RECONCILIATION = "AFTER_RECONCILIATION"


class WorkspaceErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    ID_UNAVAILABLE = "ID_UNAVAILABLE"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    ROOT_MISMATCH = "ROOT_MISMATCH"
    ROOT_UNSAFE = "ROOT_UNSAFE"
    STATE_REJECTED = "STATE_REJECTED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_REVOKED = "LEASE_REVOKED"
    FENCE_REJECTED = "FENCE_REJECTED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    QUOTA_DIVERGENT = "QUOTA_DIVERGENT"
    CLEANUP_INCOMPLETE = "CLEANUP_INCOMPLETE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    UNKNOWN = "UNKNOWN"


class ReconcileScope(StrEnum):
    ROOT = "ROOT"
    USAGE = "USAGE"
    LEASES = "LEASES"
    CLEANUP = "CLEANUP"
    ALL = "ALL"


class OpaqueWorkspaceRootRef:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = _required(value, "root_ref")

    def __hash__(self) -> int:
        return hash(self._value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, OpaqueWorkspaceRootRef) and self._value == other._value

    def __repr__(self) -> str:
        return "OpaqueWorkspaceRootRef(<opaque>)"


class FilesystemObjectIdentity:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = _required(value, "root_identity")

    def __hash__(self) -> int:
        return hash(self._value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FilesystemObjectIdentity) and self._value == other._value

    def __repr__(self) -> str:
        return "FilesystemObjectIdentity(<opaque>)"


class OpaqueRootHandleRef:
    __slots__ = ("_value", "_binding")

    def __init__(self, value: str, binding: str | None = None) -> None:
        self._value = _required(value, "root_handle_ref")
        self._binding = binding

    def __hash__(self) -> int:
        return hash((self._value, self._binding))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, OpaqueRootHandleRef) and (self._value, self._binding) == (other._value, other._binding)

    def __repr__(self) -> str:
        return "OpaqueRootHandleRef(<ephemeral>)"

    def __reduce_ex__(self, protocol: int):
        raise TypeError("root handles are ephemeral and not serializable")


class FencingToken:
    __slots__ = ("_value",)

    def __init__(self, value: int) -> None:
        if value < 1:
            raise ValueError("fencing token must be positive")
        self._value = value

    def __lt__(self, other: object) -> bool:
        return isinstance(other, FencingToken) and self._value < other._value

    def __le__(self, other: object) -> bool:
        return isinstance(other, FencingToken) and self._value <= other._value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FencingToken) and self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __repr__(self) -> str:
        return "FencingToken(<monotonic>)"


@dataclass(frozen=True, slots=True)
class WorkspaceOperationContext:
    user_id: str
    workspace_id: str
    agent_id: str
    execution_id: str
    correlation_id: str
    purpose: str
    actor: str

    def __post_init__(self) -> None:
        for name in ("user_id", "workspace_id", "agent_id", "execution_id", "correlation_id", "actor"):
            _required(getattr(self, name), name)
        _required(self.purpose, "purpose", MAX_TEXT)
        if any(token in self.workspace_id.lower() for token in ("/", "\\", "..", ":", "//")):
            raise ValueError("workspace_id must be opaque")

    def scope_key(self) -> tuple[str, ...]:
        return (self.user_id, self.workspace_id, self.agent_id, self.execution_id, self.correlation_id, self.purpose, self.actor)

    def __repr__(self) -> str:
        return f"WorkspaceOperationContext(user_id={self.user_id!r}, workspace_id={self.workspace_id!r}, agent_id={self.agent_id!r}, execution_id={self.execution_id!r}, correlation_id={self.correlation_id!r}, purpose=<bounded>, actor={self.actor!r})"


@dataclass(frozen=True, slots=True)
class CreateWorkspaceContext:
    user_id: str
    requested_workspace_id: str | None
    agent_id: str
    execution_id: str
    correlation_id: str
    purpose: str
    actor: str
    workspace_id: None = field(default=None, init=False)

    def __post_init__(self) -> None:
        for name in ("user_id", "agent_id", "execution_id", "correlation_id", "actor"):
            _required(getattr(self, name), name)
        _required(self.purpose, "purpose", MAX_TEXT)
        if self.requested_workspace_id is not None:
            _required(self.requested_workspace_id, "requested_workspace_id")
            if any(token in self.requested_workspace_id.lower() for token in ("/", "\\", "..", ":", "//")):
                raise ValueError("requested_workspace_id must be opaque")

    def scope_key(self) -> tuple[str, ...]:
        return (self.user_id, self.requested_workspace_id or "", self.agent_id, self.execution_id, self.correlation_id, self.purpose, self.actor)

    def __repr__(self) -> str:
        return f"CreateWorkspaceContext(user_id={self.user_id!r}, requested_workspace_id={self.requested_workspace_id!r}, agent_id={self.agent_id!r}, execution_id={self.execution_id!r}, correlation_id={self.correlation_id!r}, purpose=<bounded>, actor={self.actor!r})"


@dataclass(frozen=True, slots=True)
class WorkspaceQuota:
    maximum_bytes: int
    maximum_entries: int
    maximum_file_bytes: int
    maximum_depth: int
    maximum_active_leases: int
    reserved_bytes: int

    def __post_init__(self) -> None:
        values = (self.maximum_bytes, self.maximum_entries, self.maximum_file_bytes, self.maximum_depth, self.maximum_active_leases, self.reserved_bytes)
        if any(not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("quota values must be non-negative integers")
        if self.maximum_file_bytes > self.maximum_bytes:
            raise ValueError("maximum_file_bytes cannot exceed maximum_bytes")
        if self.reserved_bytes > self.maximum_bytes:
            raise ValueError("reserved_bytes cannot exceed maximum_bytes")


@dataclass(frozen=True, slots=True)
class WorkspaceUsage:
    accounted_bytes: int
    accounted_entries: int
    reserved_bytes: int
    active_leases: int
    measured_at: datetime
    reconciliation_state: UsageReconciliationState
    reserved_entries: int = 0

    def __post_init__(self) -> None:
        if any(not isinstance(value, int) or value < 0 for value in (self.accounted_bytes, self.accounted_entries, self.reserved_bytes, self.active_leases, self.reserved_entries)):
            raise ValueError("usage values must be non-negative integers")
        _aware(self.measured_at, "measured_at")
        object.__setattr__(self, "reconciliation_state", UsageReconciliationState(self.reconciliation_state))

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class WorkspaceRootDescriptor:
    workspace_id: str
    root_ref: OpaqueWorkspaceRootRef
    root_identity: FilesystemObjectIdentity
    storage_class: str
    containment_policy_version: int
    provisioned_at: datetime
    health: RootHealth

    def __post_init__(self) -> None:
        _required(self.workspace_id, "workspace_id")
        _required(self.storage_class, "storage_class", 64)
        if self.containment_policy_version < 1:
            raise ValueError("containment policy version must be positive")
        _aware(self.provisioned_at, "provisioned_at")
        object.__setattr__(self, "health", RootHealth(self.health))
    def __repr__(self) -> str:
        return f"WorkspaceRootDescriptor(workspace_id={self.workspace_id!r}, root_ref=<opaque>, root_identity=<opaque>, storage_class={self.storage_class!r}, health={self.health.value!r})"


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    workspace_id: str
    user_id: str
    display_name: str
    state: WorkspaceState
    root_descriptor: WorkspaceRootDescriptor | None
    quota: WorkspaceQuota
    configuration_ref: str
    classification: DataClassification
    version: int
    usage: WorkspaceUsage
    created_at: datetime
    activated_at: datetime | None = None
    archived_at: datetime | None = None
    deletion_requested_at: datetime | None = None
    deleted_at: datetime | None = None
    creation_idempotency_key: str | None = None
    creation_fingerprint: str | None = None
    deletion_fence: FencingToken | None = None
    deletion_checkpoint: str | None = None

    def __post_init__(self) -> None:
        for name in ("workspace_id", "user_id", "configuration_ref"):
            _required(getattr(self, name), name)
        _required(self.display_name, "display_name", MAX_TEXT)
        object.__setattr__(self, "state", WorkspaceState(self.state))
        object.__setattr__(self, "classification", DataClassification(self.classification))
        if self.version < 1:
            raise ValueError("workspace version must be positive")
        _aware(self.created_at, "created_at")
        for name in ("activated_at", "archived_at", "deletion_requested_at", "deleted_at"):
            value = getattr(self, name)
            if value is not None:
                _aware(value, name)


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    workspace_id: str
    user_id: str
    state: WorkspaceState
    classification: DataClassification
    quota: WorkspaceQuota
    usage: WorkspaceUsage | None
    version: int
    policy_version: int
    root_descriptor: WorkspaceRootDescriptor | None = None

    def __post_init__(self) -> None:
        _required(self.workspace_id, "workspace_id")
        _required(self.user_id, "user_id")
        object.__setattr__(self, "state", WorkspaceState(self.state))
        object.__setattr__(self, "classification", DataClassification(self.classification))
        if self.version < 1 or self.policy_version < 1:
            raise ValueError("versions must be positive")


@dataclass(frozen=True, slots=True)
class WorkspaceOperationBudget:
    maximum_bytes: int = 0
    maximum_entries: int = 0
    maximum_depth: int = 0
    duration: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if any(not isinstance(value, int) or value < 0 for value in (self.maximum_bytes, self.maximum_entries, self.maximum_depth)):
            raise ValueError("operation budget values must be non-negative integers")
        if self.duration <= timedelta(0) or self.duration > timedelta(hours=1):
            raise ValueError("duration must be positive and bounded")


@dataclass(frozen=True, slots=True)
class WorkspaceLease:
    lease_id: str
    workspace_id: str
    context: WorkspaceOperationContext
    permissions: tuple[WorkspacePermission, ...]
    budget: WorkspaceOperationBudget
    root_handle_ref: OpaqueRootHandleRef
    root_identity: FilesystemObjectIdentity
    workspace_version: int
    fencing_token: FencingToken
    acquired_at: datetime
    expires_at: datetime
    state: LeaseState = LeaseState.ACTIVE

    def __post_init__(self) -> None:
        _required(self.lease_id, "lease_id")
        if self.context.workspace_id != self.workspace_id:
            raise ValueError("lease context workspace mismatch")
        permissions = tuple(WorkspacePermission(permission) for permission in self.permissions)
        if not permissions or len(permissions) > MAX_PERMISSIONS or len(set(permissions)) != len(permissions):
            raise ValueError("lease permissions must be non-empty and bounded")
        object.__setattr__(self, "permissions", permissions)
        if self.expires_at <= self.acquired_at:
            raise ValueError("lease must expire after acquisition")
        _aware(self.acquired_at, "acquired_at")
        _aware(self.expires_at, "expires_at")
        object.__setattr__(self, "state", LeaseState(self.state))


@dataclass(frozen=True, slots=True)
class WorkspaceLock:
    lock_id: str
    workspace_id: str
    context: WorkspaceOperationContext
    workspace_version: int
    fencing_token: FencingToken
    expires_at: datetime
    state: LockState = LockState.ACTIVE


@dataclass(frozen=True, slots=True)
class AcquireWorkspaceLock:
    operation_id: str
    context: WorkspaceOperationContext
    requested_duration: timedelta
    expected_workspace_version: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ReleaseWorkspaceLock:
    operation_id: str
    context: WorkspaceOperationContext
    lock_id: str
    fencing_token: FencingToken
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    reservation_id: str
    workspace_id: str
    lease_id: str
    bytes_reserved: int
    entries_reserved: int
    depth: int
    workspace_version: int
    expires_at: datetime
    state: EffectState = EffectState.APPLIED


@dataclass(frozen=True, slots=True)
class WorkspaceError:
    code: WorkspaceErrorCode
    retryability: Retryability = Retryability.NEVER
    effect_state: EffectState = EffectState.NOT_APPLIED
    reason: str = "workspace operation failed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", WorkspaceErrorCode(self.code))
        object.__setattr__(self, "retryability", Retryability(self.retryability))
        object.__setattr__(self, "effect_state", EffectState(self.effect_state))
        _required(self.reason, "reason", MAX_REASON)
    def __repr__(self) -> str:
        return f"WorkspaceError(code={self.code.value!r}, retryability={self.retryability.value!r}, effect_state={self.effect_state.value!r})"


@dataclass(frozen=True, slots=True)
class WorkspaceDeletionReceipt:
    workspace_id: str
    state: WorkspaceState
    version: int
    fence: FencingToken
    effect_state: EffectState
    processed_categories: tuple[str, ...]
    checkpoint: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceReconciliationReceipt:
    workspace_id: str
    scope: ReconcileScope
    state: WorkspaceState
    version: int
    effect_state: EffectState
    inspected_entries: int
    repaired_items: int
    evidence_codes: tuple[str, ...]
    checkpoint: str | None = None


@dataclass(frozen=True, slots=True)
class CreateWorkspace:
    operation_id: str
    context: CreateWorkspaceContext
    display_name: str
    quota: WorkspaceQuota
    configuration_ref: str = "config:default"
    classification: DataClassification = DataClassification.INTERNAL
    requested_root_ref: str | None = None
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        _required(self.operation_id, "operation_id")
        _required(self.display_name, "display_name", MAX_TEXT)
        _required(self.configuration_ref, "configuration_ref")
        _required(self.idempotency_key, "idempotency_key", 256)
        object.__setattr__(self, "classification", DataClassification(self.classification))
        if self.requested_root_ref is not None:
            raise ValueError("callers cannot provide a physical or adapter root")


@dataclass(frozen=True, slots=True)
class ActivateWorkspace:
    operation_id: str
    context: WorkspaceOperationContext
    expected_version: int
    expected_root_identity: FilesystemObjectIdentity
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class InspectWorkspace:
    context: WorkspaceOperationContext
    include_usage: bool = True


@dataclass(frozen=True, slots=True)
class AcquireWorkspaceLease:
    operation_id: str
    context: WorkspaceOperationContext
    permissions: tuple[WorkspacePermission, ...]
    requested_duration: timedelta
    budget: WorkspaceOperationBudget
    expected_workspace_version: int
    expected_root_identity: FilesystemObjectIdentity
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RenewWorkspaceLease:
    operation_id: str
    context: WorkspaceOperationContext
    lease_id: str
    expected_expires_at: datetime
    requested_extension: timedelta
    expected_workspace_version: int
    expected_root_identity: FilesystemObjectIdentity
    fencing_token: FencingToken
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ReleaseWorkspaceLease:
    operation_id: str
    context: WorkspaceOperationContext
    lease_id: str
    fencing_token: FencingToken
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class TransitionWorkspace:
    operation_id: str
    context: WorkspaceOperationContext
    target_state: WorkspaceState
    expected_version: int
    drain_deadline: datetime
    reason: str
    idempotency_key: str
    fencing_token: FencingToken | None = None


@dataclass(frozen=True, slots=True)
class ReserveWorkspaceUsage:
    operation_id: str
    context: WorkspaceOperationContext
    lease_id: str
    fencing_token: FencingToken
    bytes_requested: int
    entries_requested: int
    maximum_file_bytes: int
    depth: int
    expected_workspace_version: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RecordWorkspaceUsage:
    operation_id: str
    context: WorkspaceOperationContext
    lease_id: str
    reservation_id: str
    fencing_token: FencingToken
    bytes_effective: int
    entries_effective: int
    expected_workspace_version: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ReleaseQuotaReservation:
    operation_id: str
    context: WorkspaceOperationContext
    lease_id: str
    reservation_id: str
    fencing_token: FencingToken
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DeleteWorkspace:
    operation_id: str
    context: WorkspaceOperationContext
    expected_version: int
    expected_root_identity: FilesystemObjectIdentity
    recovery_window: timedelta
    reason: str
    idempotency_key: str
    deletion_policy_ref: str = "deletion:default"


@dataclass(frozen=True, slots=True)
class ReconcileWorkspace:
    operation_id: str
    context: WorkspaceOperationContext
    expected_version: int
    scope: ReconcileScope
    maximum_entries: int
    idempotency_key: str


WorkspaceSnapshotResult = WorkspaceSnapshot | WorkspaceError
WorkspaceLeaseResult = WorkspaceLease | WorkspaceError
WorkspaceCreationResult = WorkspaceSnapshot | WorkspaceError


__all__ = [name for name in globals() if not name.startswith("_")]
