from dataclasses import dataclass, field

import pytest

from agentos.events import (
    ArchiveCursor,
    AuthorizedEventQuery,
    DataClassification,
    EventContext,
    InMemoryEventBus,
    ReplayPolicy,
    ReplayRequest,
    ReplayStatus,
    SubscriptionRef,
)
from agentos.events.in_memory import InMemoryEventArchive
from agentos.events.ports import OrderingRequirement, OwnershipScope, SubscriptionSpec, VersionRange
from agentos.events.models import DeliveryDisposition
from agentos.events.ports import EventDelivery


@dataclass
class RecordingConsumer:
    deliveries: list[EventDelivery] = field(default_factory=list)

    def handle(self, delivery: EventDelivery) -> DeliveryDisposition:
        self.deliveries.append(delivery)
        return DeliveryDisposition.ACKNOWLEDGED


def _context(user="user:1", workspace="workspace:1"):
    return EventContext(user, workspace, "agent:1", "execution:1", "correlation:1", "archive-test")


def _subscription():
    return SubscriptionSpec(
        "archive-consumer",
        ("Execution*",),
        (VersionRange(1, 1),),
        OwnershipScope("user:1", "workspace:1"),
        OrderingRequirement.NONE,
        DataClassification.INTERNAL,
        ReplayPolicy.ALLOWED,
    )


def test_query_is_paginated_and_does_not_cross_ownership(event_factory):
    archive = InMemoryEventArchive(InMemoryEventBus())
    event_one = event_factory(event_id="event:1")
    event_two = event_factory(event_id="event:2", sequence=2)
    archive.append((event_one, event_two))
    first = archive.query(AuthorizedEventQuery(_context(), limit=1))
    assert len(first.events) == 1
    assert first.next_cursor is not None
    other = archive.query(AuthorizedEventQuery(_context("user:2")))
    assert other.events == ()


def test_query_respects_agent_and_execution_context(event_factory):
    bus = InMemoryEventBus()
    archive = InMemoryEventArchive(bus)
    bus.publish(
        (
            event_factory(),
            event_factory(event_id="event:2", agent_id="agent:other", execution_id="execution:other"),
        )
    )

    page = archive.query(AuthorizedEventQuery(_context()))

    assert [event.event_id for event in page.events] == ["event:1"]


def test_replay_unknown_event_id_is_rejected_without_storage_error(event_factory):
    bus = InMemoryEventBus()
    consumer = RecordingConsumer()
    subscription_ref = bus.subscribe(_subscription(), consumer)
    archive = InMemoryEventArchive(bus)

    with pytest.raises(PermissionError):
        archive.replay(ReplayRequest(_context(), subscription_ref, event_ids=("event:missing",)))


def test_replay_preserves_identity_and_marks_operation_outside_envelope(event_factory):
    bus = InMemoryEventBus()
    consumer = RecordingConsumer()
    subscription_ref = bus.subscribe(_subscription(), consumer)
    archive = InMemoryEventArchive(bus)
    event = event_factory()
    archive.append((event,))
    job = archive.replay(ReplayRequest(_context(), subscription_ref, event_ids=(event.event_id,)))
    result = archive.run_replay(job)
    delivery = consumer.deliveries[-1]
    assert result.status is ReplayStatus.COMPLETED
    assert delivery.event == event
    assert delivery.event.event_id == event.event_id
    assert delivery.replay is True
    assert delivery.operation_ref == str(job)


def test_cancelled_replay_stops_new_deliveries_without_deleting_history(event_factory):
    bus = InMemoryEventBus()
    consumer = RecordingConsumer()
    subscription_ref = bus.subscribe(_subscription(), consumer)
    archive = InMemoryEventArchive(bus)
    event = event_factory()
    archive.append((event,))
    job = archive.replay(ReplayRequest(_context(), subscription_ref, event_ids=(event.event_id,)))
    archive.cancel_replay(job)
    result = archive.run_replay(job)
    assert result.status is ReplayStatus.CANCELLED
    assert archive.query(AuthorizedEventQuery(_context())).events


def test_expired_event_is_explicit_not_an_empty_not_found(event_factory):
    bus = InMemoryEventBus()
    archive = InMemoryEventArchive(bus)
    event = event_factory()
    archive.append((event,))
    archive.expire(event.event_id)
    page = archive.query(AuthorizedEventQuery(_context(), event_ids=(event.event_id,)))
    assert page.retention_status.name == "EXPIRED"
