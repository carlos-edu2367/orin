from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .models import (
    DataClassification,
    CommitState,
    DeliveryDisposition,
    EventContext,
    EventEnvelope,
    FailureKind,
    OrderingRequirement,
    ReplayPolicy,
    ReplayStatus,
    RetentionStatus,
    _aware,
    _required,
    classification_allows,
)

EventType = str


@dataclass(frozen=True, slots=True)
class OpaqueRef:
    value: str

    def __post_init__(self) -> None:
        _required(self.value, "reference")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class PublicationLease:
    lease_ref: str
    owner_ref: str
    expiry_tick: int

    def __post_init__(self) -> None:
        _required(self.lease_ref, "lease_ref")
        _required(self.owner_ref, "owner_ref")
        if self.expiry_tick < 1:
            raise ValueError("expiry_tick must be positive")


@dataclass(frozen=True, slots=True)
class OutboxPosition:
    value: str

    def __post_init__(self) -> None:
        _required(self.value, "outbox position")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class PublishOutboxBatch:
    publisher_ref: str
    partition_ref: str
    after_position: OutboxPosition | None
    maximum_events: int
    lease: PublicationLease
    context: EventContext | None = None
    transaction_id: str | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        _required(self.publisher_ref, "publisher_ref")
        _required(self.partition_ref, "partition_ref")
        if self.maximum_events < 1 or self.maximum_events > 1000:
            raise ValueError("maximum_events must be between 1 and 1000")
        if self.transaction_id is not None:
            _required(self.transaction_id, "transaction_id")
        if self.idempotency_key is not None:
            _required(self.idempotency_key, "idempotency_key")


@dataclass(frozen=True, slots=True)
class OutboxPublishResult:
    published_event_ids: tuple[str, ...] = ()
    pending_event_ids: tuple[str, ...] = ()
    failed_event_ids: tuple[str, ...] = ()
    duplicate_event_ids: tuple[str, ...] = ()
    next_position: OutboxPosition | None = None

    @property
    def pending_count(self) -> int:
        return len(self.pending_event_ids)


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    event: EventEnvelope
    position: OutboxPosition
    commit_state: CommitState
    transaction_id: str | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if self.transaction_id is not None:
            _required(self.transaction_id, "transaction_id")
        if self.idempotency_key is not None:
            _required(self.idempotency_key, "idempotency_key")


class ConfirmedOutboxSource(Protocol):
    def read_outbox(self, request: PublishOutboxBatch) -> tuple[OutboxRecord, ...]: ...

    def inspect_commit(self, record: OutboxRecord, request: PublishOutboxBatch) -> bool: ...


@dataclass(frozen=True, slots=True)
class PublishReceipt:
    published_event_ids: tuple[str, ...] = ()
    duplicate_event_ids: tuple[str, ...] = ()
    rejected_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VersionRange:
    minimum: int
    maximum: int | None = None

    def __post_init__(self) -> None:
        if self.minimum < 1:
            raise ValueError("minimum event version must be positive")
        if self.maximum is not None and (self.maximum < self.minimum or self.maximum < 1):
            raise ValueError("maximum event version must be >= minimum")

    def accepts(self, version: int) -> bool:
        return version >= self.minimum and (self.maximum is None or version <= self.maximum)


@dataclass(frozen=True, slots=True)
class OwnershipScope:
    user_id: str
    workspace_id: str | None = None
    agent_id: str | None = None
    execution_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.user_id, "ownership user_id")
        for field in ("workspace_id", "agent_id", "execution_id"):
            value = getattr(self, field)
            if value is not None:
                _required(value, f"ownership {field}")

    def accepts(self, event: EventEnvelope) -> bool:
        return (
            event.user_id == self.user_id
            and (self.workspace_id is None or event.workspace_id == self.workspace_id)
            and (self.agent_id is None or event.agent_id == self.agent_id)
            and (self.execution_id is None or event.execution_id == self.execution_id)
        )


@dataclass(frozen=True, slots=True)
class SubscriptionSpec:
    consumer_name: str
    accepted_event_types: tuple[str, ...]
    accepted_versions: tuple[VersionRange, ...]
    ownership_scope: OwnershipScope
    ordering_requirement: OrderingRequirement
    data_clearance: DataClassification
    replay_policy: ReplayPolicy

    def __post_init__(self) -> None:
        _required(self.consumer_name, "consumer_name")
        if not self.accepted_event_types:
            raise ValueError("accepted_event_types cannot be empty")
        if not self.accepted_versions:
            raise ValueError("accepted_versions cannot be empty")
        for event_type in self.accepted_event_types:
            _required(event_type, "accepted event type")
        try:
            object.__setattr__(self, "data_clearance", DataClassification(self.data_clearance))
            object.__setattr__(self, "ordering_requirement", OrderingRequirement(self.ordering_requirement))
            object.__setattr__(self, "replay_policy", ReplayPolicy(self.replay_policy))
        except ValueError as exc:
            raise ValueError("invalid subscription policy") from exc

    def accepts(self, event: EventEnvelope) -> bool:
        return (
            self.matches_type(event.event_type)
            and self.accepts_version(event.event_version)
            and self.ownership_scope.accepts(event)
            and classification_allows(self.data_clearance, event.classification)
        )

    def matches_type(self, event_type: str) -> bool:
        return any(
            (pattern.endswith("*") and event_type.startswith(pattern[:-1])) or pattern == event_type
            for pattern in self.accepted_event_types
        )

    def accepts_version(self, event_version: int) -> bool:
        return any(version.accepts(event_version) for version in self.accepted_versions)


@dataclass(frozen=True, slots=True)
class SubscriptionRef:
    value: str

    def __post_init__(self) -> None:
        _required(self.value, "subscription_ref")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class EventDelivery:
    event: EventEnvelope
    delivery_id: str
    attempt: int
    replay: bool = False
    operation_ref: str | None = None

    def __post_init__(self) -> None:
        _required(self.delivery_id, "delivery_id")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        if self.operation_ref is not None:
            _required(self.operation_ref, "operation_ref")

    def __repr__(self) -> str:
        return (
            "EventDelivery("
            f"event_id={self.event.event_id!r}, delivery_id={self.delivery_id!r}, "
            f"attempt={self.attempt}, replay={self.replay})"
        )


@dataclass(frozen=True, slots=True)
class DeliveryFailure:
    kind: FailureKind
    code: str
    summary: str

    def __post_init__(self) -> None:
        _required(self.code, "failure code")
        _required(self.summary, "failure summary")
        if len(self.summary) > 256:
            raise ValueError("failure summary is too long")

    def __repr__(self) -> str:
        return f"DeliveryFailure(kind={self.kind.value!r}, code={self.code!r})"


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    quarantine_ref: str
    subscription_ref: SubscriptionRef
    event_id: str
    failure: DeliveryFailure
    attempt: int

    def __post_init__(self) -> None:
        _required(self.quarantine_ref, "quarantine_ref")
        _required(self.event_id, "event_id")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")


@dataclass(frozen=True, slots=True)
class EventAuditRecord:
    audit_ref: str
    subscription_ref: SubscriptionRef
    event_id: str
    code: str

    def __post_init__(self) -> None:
        _required(self.audit_ref, "audit_ref")
        _required(self.event_id, "event_id")
        _required(self.code, "audit code")


@dataclass(frozen=True, slots=True)
class DeliveryBatchResult:
    acknowledged: tuple[str, ...] = ()
    retried: tuple[str, ...] = ()
    quarantined: tuple[str, ...] = ()
    attempts: tuple[EventDelivery, ...] = ()
    reconciliation: tuple["SequenceGap", ...] = ()


@dataclass(frozen=True, slots=True)
class SequenceGap:
    execution_id: str
    expected_sequence: int
    received_sequence: int

    @property
    def missing_sequence(self) -> int:
        return self.expected_sequence


@dataclass(frozen=True, slots=True)
class ArchiveCursor:
    value: str

    def __post_init__(self) -> None:
        _required(self.value, "archive cursor")


@dataclass(frozen=True, slots=True)
class AuthorizedEventQuery:
    context: EventContext
    limit: int = 100
    cursor: ArchiveCursor | None = None
    event_type: str | None = None
    event_version: int | None = None
    classification: DataClassification | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None
    event_ids: tuple[str, ...] = ()
    clearance: DataClassification = DataClassification.RESTRICTED

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > 1000:
            raise ValueError("query limit must be between 1 and 1000")
        if self.event_type is not None:
            _required(self.event_type, "event_type")
        if self.event_version is not None and self.event_version < 1:
            raise ValueError("event_version must be positive")
        if self.classification is not None:
            object.__setattr__(self, "classification", DataClassification(self.classification))
        object.__setattr__(self, "clearance", DataClassification(self.clearance))
        for event_id in self.event_ids:
            _required(event_id, "event_id")
        for field in ("occurred_after", "occurred_before"):
            value = getattr(self, field)
            if value is not None:
                _aware(value, field)


@dataclass(frozen=True, slots=True)
class EventPage:
    events: tuple[EventEnvelope, ...]
    next_cursor: ArchiveCursor | None
    retention_status: RetentionStatus = RetentionStatus.AVAILABLE


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    context: EventContext
    subscription_ref: SubscriptionRef
    event_ids: tuple[str, ...] = ()
    cursor: ArchiveCursor | None = None
    purpose: str = "event-replay"

    def __post_init__(self) -> None:
        _required(self.purpose, "purpose")
        if bool(self.event_ids) == (self.cursor is not None):
            raise ValueError("replay requires event_ids or cursor, but not both")
        for event_id in self.event_ids:
            _required(event_id, "event_id")


@dataclass(frozen=True, slots=True)
class ReplayJobRef:
    value: str

    def __post_init__(self) -> None:
        _required(self.value, "replay_job_ref")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ReplayResult:
    job_ref: ReplayJobRef
    status: ReplayStatus
    delivered_event_ids: tuple[str, ...] = ()
    pending_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CancellationResult:
    reference: str
    cancelled: bool

    def __post_init__(self) -> None:
        _required(self.reference, "reference")


class EventConsumer(Protocol):
    def handle(self, delivery: EventDelivery) -> DeliveryDisposition: ...


class OutboxPublisher(Protocol):
    def publish_pending(self, request: PublishOutboxBatch) -> OutboxPublishResult: ...


class EventBus(Protocol):
    def publish(self, batch: tuple[EventEnvelope, ...]) -> PublishReceipt: ...

    def subscribe(self, subscription: SubscriptionSpec, consumer: EventConsumer) -> SubscriptionRef: ...


class EventArchive(Protocol):
    def query(self, query: AuthorizedEventQuery) -> EventPage: ...

    def replay(self, request: ReplayRequest) -> ReplayJobRef: ...
