from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from agentos.events.models import DataClassification, EventEnvelope
from agentos.events.security import freeze_payload

from .security import freeze_persistence_payload


MAX_PAGE_SIZE = 100
MAX_FILTERS = 16
MAX_PURPOSE_LENGTH = 128


def _required(value: object, field_name: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{field_name} exceeds its maximum length")
    return value


class ConsistencyLevel(StrEnum):
    EVENTUAL = "EVENTUAL"
    SESSION = "SESSION"
    STRONG = "STRONG"


class IsolationLevel(StrEnum):
    READ_COMMITTED = "READ_COMMITTED"
    REPEATABLE_READ = "REPEATABLE_READ"
    SERIALIZABLE = "SERIALIZABLE"


class CommitState(StrEnum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    UNKNOWN = "UNKNOWN"


class Retryability(StrEnum):
    NEVER = "NEVER"
    SAFE = "SAFE"
    POLICY_DEPENDENT = "POLICY_DEPENDENT"


class PersistenceErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    DUPLICATE_OUTBOX_EVENT = "DUPLICATE_OUTBOX_EVENT"
    CONSTRAINT_VIOLATION = "CONSTRAINT_VIOLATION"
    DEADLOCK = "DEADLOCK"
    SERIALIZATION_FAILURE = "SERIALIZATION_FAILURE"
    TIMEOUT = "TIMEOUT"
    CONNECTION = "CONNECTION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class PersistenceOperationContext:
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
        _required(self.purpose, "purpose", maximum=MAX_PURPOSE_LENGTH)

    def scope_key(self) -> tuple[str, ...]:
        return (
            self.user_id,
            self.workspace_id or "",
            self.agent_id,
            self.execution_id,
            self.correlation_id,
            self.purpose,
            self.actor,
        )

    def __repr__(self) -> str:
        return (
            "PersistenceOperationContext("
            f"user_id={self.user_id!r}, workspace_id={self.workspace_id!r}, "
            f"agent_id={self.agent_id!r}, execution_id={self.execution_id!r}, "
            f"correlation_id={self.correlation_id!r}, purpose=<bounded>, actor={self.actor!r})"
        )


@dataclass(frozen=True, slots=True)
class TransactionOptions:
    consistency: ConsistencyLevel = ConsistencyLevel.STRONG
    isolation: IsolationLevel = IsolationLevel.READ_COMMITTED
    timeout: timedelta = timedelta(seconds=30)
    read_only: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "consistency", ConsistencyLevel(self.consistency))
        object.__setattr__(self, "isolation", IsolationLevel(self.isolation))
        if self.timeout <= timedelta(0):
            raise ValueError("timeout must be positive")
        if self.timeout > timedelta(hours=1):
            raise ValueError("timeout exceeds the public maximum")


@dataclass(frozen=True, slots=True)
class RecordReference:
    value: str

    def __post_init__(self) -> None:
        _required(self.value, "record_ref")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return "RecordReference(<opaque>)"


@dataclass(frozen=True, slots=True)
class VersionReference:
    value: int

    def __post_init__(self) -> None:
        if self.value < 1:
            raise ValueError("version must be positive")

    def __repr__(self) -> str:
        return "VersionReference(<opaque>)"


@dataclass(frozen=True, slots=True)
class OutboxReference:
    value: str

    def __post_init__(self) -> None:
        _required(self.value, "outbox_ref")

    def __repr__(self) -> str:
        return "OutboxReference(<opaque>)"


@dataclass(frozen=True, slots=True)
class ExpectedVersion:
    record_ref: RecordReference
    version: int

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("expected version must be positive")


@dataclass(frozen=True, slots=True)
class RecordChange:
    record_ref: RecordReference
    record_type: str
    expected_version: int | None
    data: Mapping[str, object]
    classification: DataClassification

    def __post_init__(self) -> None:
        _required(self.record_type, "record_type", maximum=96)
        if self.expected_version is not None and self.expected_version < 1:
            raise ValueError("expected_version must be positive when supplied")
        object.__setattr__(self, "classification", DataClassification(self.classification))
        object.__setattr__(self, "data", freeze_persistence_payload(self.data))


@dataclass(frozen=True, slots=True)
class AuditChange:
    audit_ref: str
    record_ref: RecordReference
    decision: str
    resulting_version: int
    fields: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required(self.audit_ref, "audit_ref", maximum=128)
        _required(self.decision, "decision", maximum=64)
        if self.resulting_version < 1:
            raise ValueError("resulting_version must be positive")
        object.__setattr__(self, "fields", freeze_payload(self.fields))


@dataclass(frozen=True, slots=True)
class OutboxChange:
    event: EventEnvelope
    source_record_ref: RecordReference
    expected_source_version: int

    def __post_init__(self) -> None:
        if str(self.event.event_id) != self.event.event_id.strip():
            raise ValueError("event_id must be non-blank")
        if self.expected_source_version < 1:
            raise ValueError("expected_source_version must be positive")

    @property
    def outbox_ref(self) -> OutboxReference:
        return OutboxReference(str(self.event.event_id))


@dataclass(frozen=True, slots=True)
class TransactionRequest:
    transaction_id: str
    context: PersistenceOperationContext
    options: TransactionOptions
    idempotency_key: str
    fingerprint: str
    expected_versions: tuple[ExpectedVersion, ...]
    changes: tuple[RecordChange, ...]
    audit: tuple[AuditChange, ...]
    outbox: tuple[OutboxChange, ...]

    def __post_init__(self) -> None:
        _required(self.transaction_id, "transaction_id", maximum=128)
        _required(self.idempotency_key, "idempotency_key", maximum=256)
        _required(self.fingerprint, "fingerprint", maximum=128)
        if not isinstance(self.options, TransactionOptions):
            raise ValueError("options must be TransactionOptions")


@dataclass(frozen=True, slots=True)
class AuthorizedRecord:
    record_ref: RecordReference
    record_type: str
    version: int
    context: PersistenceOperationContext
    classification: DataClassification
    data: Mapping[str, object]

    def __post_init__(self) -> None:
        _required(self.record_type, "record_type", maximum=96)
        if self.version < 1:
            raise ValueError("version must be positive")
        object.__setattr__(self, "classification", DataClassification(self.classification))
        object.__setattr__(self, "data", freeze_persistence_payload(self.data))


@dataclass(frozen=True, slots=True)
class NotFound:
    record_ref: RecordReference | None = None

    def __repr__(self) -> str:
        return "NotFound()"


@dataclass(frozen=True, slots=True)
class PageRequest:
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between one and {MAX_PAGE_SIZE}")
        if self.cursor is not None:
            _required(self.cursor, "cursor", maximum=512)

    def __repr__(self) -> str:
        return f"PageRequest(limit={self.limit}, cursor=<opaque>)"


@dataclass(frozen=True, slots=True)
class AuthorizedRead:
    context: PersistenceOperationContext
    record_ref: RecordReference
    record_type: str
    classification_ceiling: DataClassification

    def __post_init__(self) -> None:
        _required(self.record_type, "record_type", maximum=96)
        object.__setattr__(self, "classification_ceiling", DataClassification(self.classification_ceiling))


@dataclass(frozen=True, slots=True)
class AuthorizedScan:
    context: PersistenceOperationContext
    record_type: str
    filters: Mapping[str, object]
    classification_ceiling: DataClassification
    page: PageRequest = field(default_factory=PageRequest)

    def __post_init__(self) -> None:
        _required(self.record_type, "record_type", maximum=96)
        if len(self.filters) > MAX_FILTERS:
            raise ValueError("filters exceed the public maximum")
        object.__setattr__(self, "filters", freeze_payload(self.filters))
        object.__setattr__(self, "classification_ceiling", DataClassification(self.classification_ceiling))


@dataclass(frozen=True, slots=True)
class AuthorizedRecordPage:
    items: tuple[AuthorizedRecord, ...]
    next_cursor: str | None
    store_revision: int

    def __post_init__(self) -> None:
        if self.store_revision < 0:
            raise ValueError("store_revision cannot be negative")
        if self.next_cursor is not None:
            _required(self.next_cursor, "next_cursor", maximum=512)


@dataclass(frozen=True, slots=True)
class TransactionReceipt:
    transaction_id: str
    commit_state: CommitState
    record_refs: tuple[RecordReference, ...]
    outbox_refs: tuple[OutboxReference, ...]
    store_revision: int
    committed_at: datetime | None

    def __post_init__(self) -> None:
        _required(self.transaction_id, "transaction_id", maximum=128)
        object.__setattr__(self, "commit_state", CommitState(self.commit_state))
        if self.store_revision < 0:
            raise ValueError("store_revision cannot be negative")
        if self.committed_at is not None and (
            self.committed_at.tzinfo is None or self.committed_at.utcoffset() is None
        ):
            raise ValueError("committed_at must be timezone-aware")

    def __repr__(self) -> str:
        return (
            "TransactionReceipt("
            f"commit_state={self.commit_state.value!r}, records={len(self.record_refs)}, "
            f"outbox={len(self.outbox_refs)}, store_revision={self.store_revision})"
        )


@dataclass(frozen=True, slots=True)
class VersionConflict:
    record_ref: RecordReference
    expected_version: int | None
    actual_version: int | None


@dataclass(frozen=True, slots=True)
class TransactionCommitted:
    receipt: TransactionReceipt
    records: tuple[AuthorizedRecord, ...]
    already_applied: bool = False


@dataclass(frozen=True, slots=True)
class TransactionRejected:
    code: PersistenceErrorCode
    retryability: Retryability = Retryability.NEVER
    transaction_id: str | None = None
    receipt: TransactionReceipt | None = None

    def __repr__(self) -> str:
        return (
            "TransactionRejected("
            f"code={self.code.value!r}, retryability={self.retryability.value!r}, "
            "transaction_id=<opaque>)"
        )


@dataclass(frozen=True, slots=True)
class TransactionConflicted:
    conflicts: tuple[VersionConflict, ...]


@dataclass(frozen=True, slots=True)
class TransactionIndeterminate:
    transaction_id: str

    def __repr__(self) -> str:
        return "TransactionIndeterminate(transaction_id=<opaque>)"


TransactionResult = (
    TransactionCommitted | TransactionRejected | TransactionConflicted | TransactionIndeterminate
)


@dataclass(frozen=True, slots=True)
class InspectCommit:
    context: PersistenceOperationContext
    transaction_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        _required(self.transaction_id, "transaction_id", maximum=128)
        _required(self.idempotency_key, "idempotency_key", maximum=256)


def as_plain_mapping(value: Mapping[str, object]) -> dict[str, object]:
    """Return a JSON-safe copy for adapters without exposing mapping internals."""
    def thaw(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): thaw(nested) for key, nested in item.items()}
        if isinstance(item, tuple):
            return [thaw(nested) for nested in item]
        return item

    return {str(key): thaw(item) for key, item in value.items()}


__all__ = [
    "AuthorizedRead",
    "AuthorizedRecord",
    "AuthorizedRecordPage",
    "AuthorizedScan",
    "AuditChange",
    "CommitState",
    "ConsistencyLevel",
    "ExpectedVersion",
    "InspectCommit",
    "IsolationLevel",
    "MAX_PAGE_SIZE",
    "NotFound",
    "OutboxChange",
    "OutboxReference",
    "PageRequest",
    "PersistenceErrorCode",
    "PersistenceOperationContext",
    "RecordChange",
    "RecordReference",
    "Retryability",
    "TransactionCommitted",
    "TransactionConflicted",
    "TransactionIndeterminate",
    "TransactionOptions",
    "TransactionReceipt",
    "TransactionRejected",
    "TransactionRequest",
    "TransactionResult",
    "VersionConflict",
    "VersionReference",
    "as_plain_mapping",
]
