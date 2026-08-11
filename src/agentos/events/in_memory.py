from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    CommitState,
    DeliveryDisposition,
    EventEnvelope,
    FailureKind,
    OrderingRequirement,
    ReplayStatus,
    RetentionStatus,
    classification_allows,
)
from .ports import (
    CancellationResult,
    DeliveryBatchResult,
    DeliveryFailure,
    EventConsumer,
    EventAuditRecord,
    EventDelivery,
    EventBus,
    OutboxPublishResult,
    OutboxRecord,
    PublishOutboxBatch,
    QuarantineRecord,
    SequenceGap,
    SubscriptionRef,
    SubscriptionSpec,
    PublishReceipt,
    ReplayJobRef,
    ReplayRequest,
    ReplayResult,
    AuthorizedEventQuery,
    ArchiveCursor,
)


@dataclass(frozen=True, slots=True)
class _PendingDelivery:
    event_id: str
    replay: bool = False
    operation_ref: str | None = None


@dataclass(slots=True)
class _SubscriptionState:
    spec: SubscriptionSpec
    consumer: EventConsumer
    active: bool = True
    pending: dict[str, _PendingDelivery] = field(default_factory=dict)
    acknowledged: set[str] = field(default_factory=set)
    attempts: dict[str, int] = field(default_factory=dict)
    cursors: dict[str, int] = field(default_factory=dict)


class InMemoryEventBus(EventBus):
    """Deterministic test adapter implementing RFC 103 delivery semantics."""

    def __init__(self) -> None:
        self._events: dict[str, EventEnvelope] = {}
        self._event_order: list[str] = []
        self._subscriptions: dict[str, _SubscriptionState] = {}
        self._next_subscription = 1
        self._next_delivery = 1
        self._next_quarantine = 1
        self._next_audit = 1
        self._quarantine: list[QuarantineRecord] = []
        self.audit_log: list[EventAuditRecord] = []
        self._archives: list[object] = []

    @property
    def events(self) -> tuple[EventEnvelope, ...]:
        return tuple(self._events[event_id] for event_id in self._event_order)

    @property
    def quarantine(self) -> tuple[QuarantineRecord, ...]:
        return tuple(self._quarantine)

    def publish(self, batch: tuple[EventEnvelope, ...]) -> PublishReceipt:
        published: list[str] = []
        duplicates: list[str] = []
        for event in batch:
            existing = self._events.get(event.event_id)
            if existing is not None:
                if existing != event:
                    raise ValueError("event_id already exists with a different envelope")
                duplicates.append(event.event_id)
                continue
            self._events[event.event_id] = event
            self._event_order.append(event.event_id)
            published.append(event.event_id)
            for subscription_ref, state in self._subscriptions.items():
                if state.spec.accepts(event):
                    self._enqueue(state, event.event_id)
                else:
                    self._audit_policy(SubscriptionRef(subscription_ref), state.spec, event)
            for archive in self._archives:
                archive._record_from_bus(event)
        return PublishReceipt(tuple(published), tuple(duplicates))

    def store_history(self, event: EventEnvelope) -> None:
        if event.event_id in self._events:
            if self._events[event.event_id] != event:
                raise ValueError("event_id already exists with a different envelope")
            return
        self._events[event.event_id] = event
        self._event_order.append(event.event_id)

    def attach_archive(self, archive: object) -> None:
        if archive not in self._archives:
            self._archives.append(archive)

    def subscribe(self, subscription: SubscriptionSpec, consumer: EventConsumer) -> SubscriptionRef:
        if not callable(getattr(consumer, "handle", None)):
            raise TypeError("consumer must provide handle(delivery)")
        ref = SubscriptionRef(f"subscription:{self._next_subscription}")
        self._next_subscription += 1
        self._subscriptions[ref.value] = _SubscriptionState(subscription, consumer)
        return ref

    def cancel_subscription(self, subscription_ref: SubscriptionRef) -> CancellationResult:
        state = self._subscriptions.get(subscription_ref.value)
        if state is None:
            raise LookupError(subscription_ref.value)
        state.active = False
        state.pending.clear()
        return CancellationResult(subscription_ref.value, True)

    def deliver_pending(self) -> DeliveryBatchResult:
        acknowledged: list[str] = []
        retried: list[str] = []
        quarantined: list[str] = []
        attempts: list[EventDelivery] = []
        reconciliation: list[SequenceGap] = []

        for subscription_ref, state in self._subscriptions.items():
            if not state.active:
                continue
            for pending in tuple(state.pending.values()):
                event = self._events[pending.event_id]
                if not self._is_ready(state, event, reconciliation):
                    continue
                attempt_number = state.attempts.get(event.event_id, 0) + 1
                state.attempts[event.event_id] = attempt_number
                delivery = EventDelivery(
                    event=event,
                    delivery_id=f"delivery:{self._next_delivery}",
                    attempt=attempt_number,
                    replay=pending.replay,
                    operation_ref=pending.operation_ref,
                )
                self._next_delivery += 1
                attempts.append(delivery)
                disposition = self._invoke(state.consumer, delivery)
                if disposition is DeliveryDisposition.ACKNOWLEDGED:
                    state.acknowledged.add(event.event_id)
                    state.pending.pop(event.event_id, None)
                    self._advance_cursor(state, event)
                    if subscription_ref not in acknowledged:
                        acknowledged.append(subscription_ref)
                elif disposition is DeliveryDisposition.RETRYABLE_FAILURE:
                    if subscription_ref not in retried:
                        retried.append(subscription_ref)
                else:
                    state.pending.pop(event.event_id, None)
                    record = QuarantineRecord(
                        quarantine_ref=f"quarantine:{self._next_quarantine}",
                        subscription_ref=SubscriptionRef(subscription_ref),
                        event_id=event.event_id,
                        failure=DeliveryFailure(
                            FailureKind.PERMANENT,
                            "CONSUMER_REJECTED",
                            "consumer permanently rejected delivery",
                        ),
                        attempt=attempt_number,
                    )
                    self._next_quarantine += 1
                    self._quarantine.append(record)
                    if subscription_ref not in quarantined:
                        quarantined.append(subscription_ref)

        return DeliveryBatchResult(
            tuple(acknowledged), tuple(retried), tuple(quarantined), tuple(attempts), tuple(reconciliation)
        )

    def retry(self) -> DeliveryBatchResult:
        return self.deliver_pending()

    def enqueue_replay(
        self, subscription_ref: SubscriptionRef, event: EventEnvelope, operation_ref: ReplayJobRef
    ) -> None:
        state = self._subscriptions.get(subscription_ref.value)
        if state is None:
            raise LookupError(subscription_ref.value)
        if not state.active:
            raise PermissionError("subscription is cancelled")
        if not state.spec.accepts(event):
            raise PermissionError("subscription does not accept this event")
        if event.event_id in state.acknowledged:
            return
        state.pending.setdefault(
            event.event_id,
            _PendingDelivery(event.event_id, replay=True, operation_ref=operation_ref.value),
        )

    def subscription_spec(self, subscription_ref: SubscriptionRef) -> SubscriptionSpec:
        state = self._subscriptions.get(subscription_ref.value)
        if state is None:
            raise LookupError(subscription_ref.value)
        return state.spec

    def is_subscription_active(self, subscription_ref: SubscriptionRef) -> bool:
        state = self._subscriptions.get(subscription_ref.value)
        return state is not None and state.active

    def is_acknowledged(self, subscription_ref: SubscriptionRef, event_id: str) -> bool:
        state = self._subscriptions.get(subscription_ref.value)
        return state is not None and event_id in state.acknowledged

    def _enqueue(self, state: _SubscriptionState, event_id: str) -> None:
        event = self._events[event_id]
        if state.active and state.spec.accepts(event) and event_id not in state.acknowledged:
            state.pending.setdefault(event_id, _PendingDelivery(event_id))

    def _audit_policy(self, subscription_ref: SubscriptionRef, spec: SubscriptionSpec, event: EventEnvelope) -> None:
        if not spec.matches_type(event.event_type):
            code = "EVENT_TYPE_FILTERED"
        elif not spec.accepts_version(event.event_version):
            code = "EVENT_VERSION_INCOMPATIBLE"
        elif not spec.ownership_scope.accepts(event):
            code = "OWNERSHIP_REJECTED"
        else:
            code = "CLASSIFICATION_REJECTED"
        self.audit_log.append(
            EventAuditRecord(
                audit_ref=f"audit:{self._next_audit}",
                subscription_ref=subscription_ref,
                event_id=event.event_id,
                code=code,
            )
        )
        self._next_audit += 1

    @staticmethod
    def _is_ready(
        state: _SubscriptionState, event: EventEnvelope, reconciliation: list[SequenceGap]
    ) -> bool:
        if state.spec.ordering_requirement is not OrderingRequirement.PER_EXECUTION:
            return True
        if event.execution_id is None:
            return True
        assert event.sequence is not None
        expected = state.cursors.get(event.execution_id, 0) + 1
        if event.sequence < expected:
            state.pending.pop(event.event_id, None)
            state.acknowledged.add(event.event_id)
            return False
        if event.sequence > expected:
            gap = SequenceGap(event.execution_id, expected, event.sequence)
            if gap not in reconciliation:
                reconciliation.append(gap)
            return False
        return True

    @staticmethod
    def _advance_cursor(state: _SubscriptionState, event: EventEnvelope) -> None:
        if event.execution_id is not None and event.sequence is not None:
            state.cursors[event.execution_id] = max(
                state.cursors.get(event.execution_id, 0), event.sequence
            )

    @staticmethod
    def _invoke(consumer: EventConsumer, delivery: EventDelivery) -> DeliveryDisposition:
        try:
            disposition = consumer.handle(delivery)
        except Exception:
            return DeliveryDisposition.RETRYABLE_FAILURE
        if not isinstance(disposition, DeliveryDisposition):
            return DeliveryDisposition.PERMANENT_FAILURE
        return disposition


class InMemoryOutboxPublisher:
    """Publishes only records confirmed by a narrow outbox source port."""

    def __init__(self, source, bus: EventBus) -> None:
        self._source = source
        self._bus = bus

    def publish_pending(self, request: PublishOutboxBatch) -> OutboxPublishResult:
        records: tuple[OutboxRecord, ...] = self._source.read_outbox(request)
        ready: list[OutboxRecord] = []
        pending: list[str] = []
        failed: list[str] = []
        for record in records:
            if record.commit_state is CommitState.COMMITTED:
                ready.append(record)
            elif record.commit_state is CommitState.UNKNOWN:
                if self._source.inspect_commit(record, request):
                    ready.append(record)
                else:
                    pending.append(record.event.event_id)
            else:
                failed.append(record.event.event_id)

        receipt = self._bus.publish(tuple(record.event for record in ready))
        pending_ids = set(pending) | set(failed) | set(receipt.rejected_event_ids)
        next_position = None
        for record in records:
            if record.event.event_id in pending_ids:
                break
            next_position = record.position
        return OutboxPublishResult(
            published_event_ids=receipt.published_event_ids,
            pending_event_ids=tuple(pending),
            failed_event_ids=tuple(failed) + tuple(receipt.rejected_event_ids),
            duplicate_event_ids=receipt.duplicate_event_ids,
            next_position=next_position,
        )


@dataclass(slots=True)
class _ReplayState:
    request: ReplayRequest
    events: tuple[EventEnvelope, ...]
    status: ReplayStatus


class InMemoryEventArchive:
    """Bounded archive adapter with authorized query and replay."""

    def __init__(self, bus: InMemoryEventBus) -> None:
        self._bus = bus
        self._events: dict[str, EventEnvelope] = {}
        self._order: list[str] = []
        self._expired: set[str] = set()
        self._jobs: dict[str, _ReplayState] = {}
        self._next_job = 1
        self._bus.attach_archive(self)
        for event in bus.events:
            self._record_from_bus(event)

    def append(self, batch: tuple[EventEnvelope, ...]) -> None:
        for event in batch:
            self._bus.store_history(event)
            self._record_from_bus(event)

    def _record_from_bus(self, event: EventEnvelope) -> None:
        existing = self._events.get(event.event_id)
        if existing is not None:
            if existing != event:
                raise ValueError("event_id already exists with a different envelope")
            return
        self._events[event.event_id] = event
        self._order.append(event.event_id)

    def expire(self, event_id: str) -> None:
        if event_id not in self._events:
            raise LookupError(event_id)
        self._expired.add(event_id)

    def query(self, query: AuthorizedEventQuery):
        if query.event_ids and any(event_id in self._expired for event_id in query.event_ids):
            return self._page((), None, RetentionStatus.EXPIRED)
        candidates = [self._events[event_id] for event_id in self._order if event_id not in self._expired]
        filtered = [event for event in candidates if self._query_accepts(query, event)]
        start = self._cursor_offset(query.cursor)
        page_events = tuple(filtered[start : start + query.limit])
        next_cursor = ArchiveCursor(f"cursor:{start + query.limit}") if start + query.limit < len(filtered) else None
        status = RetentionStatus.AVAILABLE if page_events else RetentionStatus.EMPTY
        return self._page(page_events, next_cursor, status)

    def replay(self, request: ReplayRequest) -> ReplayJobRef:
        spec = self._bus.subscription_spec(request.subscription_ref)
        if spec.replay_policy.value != "ALLOWED":
            raise PermissionError("replay is not allowed for this subscription")
        if not self._bus.is_subscription_active(request.subscription_ref):
            raise PermissionError("subscription is cancelled")
        selected, status = self._select_replay_events(request)
        job = ReplayJobRef(f"replay:{self._next_job}")
        self._next_job += 1
        self._jobs[job.value] = _ReplayState(request, selected, status)
        return job

    def cancel_replay(self, job_ref: ReplayJobRef):
        state = self._jobs.get(job_ref.value)
        if state is None:
            raise LookupError(job_ref.value)
        if state.status not in (ReplayStatus.COMPLETED, ReplayStatus.EXPIRED):
            state.status = ReplayStatus.CANCELLED
        return state.status

    def run_replay(self, job_ref: ReplayJobRef) -> ReplayResult:
        state = self._jobs.get(job_ref.value)
        if state is None:
            raise LookupError(job_ref.value)
        if state.status in (ReplayStatus.CANCELLED, ReplayStatus.EXPIRED):
            return ReplayResult(job_ref, state.status)
        state.status = ReplayStatus.RUNNING
        for event in state.events:
            self._bus.enqueue_replay(
                subscription_ref=state.request.subscription_ref, event=event, operation_ref=job_ref
            )
        delivery_result = self._bus.deliver_pending()
        delivered = tuple(
            delivery.event.event_id
            for delivery in delivery_result.attempts
            if delivery.replay
            and self._bus.is_acknowledged(state.request.subscription_ref, delivery.event.event_id)
        )
        delivered = tuple(dict.fromkeys(delivered))
        state.status = ReplayStatus.COMPLETED if not delivery_result.retried else ReplayStatus.RUNNING
        return ReplayResult(job_ref, state.status, delivered_event_ids=delivered)

    def _select_replay_events(self, request: ReplayRequest) -> tuple[tuple[EventEnvelope, ...], ReplayStatus]:
        if request.event_ids:
            if any(event_id in self._expired for event_id in request.event_ids):
                return (), ReplayStatus.EXPIRED
            if any(event_id not in self._events for event_id in request.event_ids):
                raise PermissionError("replay target is not authorized")
            candidates = tuple(self._events[event_id] for event_id in request.event_ids)
        else:
            start = self._cursor_offset(request.cursor)
            candidates = tuple(
                self._events[event_id]
                for event_id in self._order[start:]
                if event_id not in self._expired
            )
        authorized = tuple(event for event in candidates if self._authorized(request.context, event))
        if not authorized:
            raise PermissionError("replay target is not authorized")
        return authorized, ReplayStatus.ACCEPTED

    @staticmethod
    def _authorized(context, event: EventEnvelope) -> bool:
        return (
            context.user_id == event.user_id
            and context.workspace_id == event.workspace_id
            and (event.agent_id is None or context.agent_id == event.agent_id)
            and (event.execution_id is None or context.execution_id == event.execution_id)
        )

    @staticmethod
    def _query_accepts(query: AuthorizedEventQuery, event: EventEnvelope) -> bool:
        return (
            event.user_id == query.context.user_id
            and event.workspace_id == query.context.workspace_id
            and classification_allows(query.clearance, event.classification)
            and (event.agent_id is None or event.agent_id == query.context.agent_id)
            and (event.execution_id is None or event.execution_id == query.context.execution_id)
            and (not query.event_ids or event.event_id in query.event_ids)
            and (query.event_type is None or event.event_type == query.event_type)
            and (query.event_version is None or event.event_version == query.event_version)
            and (query.classification is None or event.classification == query.classification)
            and (query.occurred_after is None or event.occurred_at > query.occurred_after)
            and (query.occurred_before is None or event.occurred_at < query.occurred_before)
        )

    @staticmethod
    def _cursor_offset(cursor: ArchiveCursor | None) -> int:
        if cursor is None:
            return 0
        if not cursor.value.startswith("cursor:"):
            raise ValueError("invalid archive cursor")
        try:
            return max(0, int(cursor.value.split(":", 1)[1]))
        except ValueError as exc:
            raise ValueError("invalid archive cursor") from exc

    @staticmethod
    def _page(events, cursor, status):
        from .ports import EventPage

        return EventPage(tuple(events), cursor, status)
