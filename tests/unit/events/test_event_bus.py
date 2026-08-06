from dataclasses import dataclass, field

from agentos.events import (
    DataClassification,
    DeliveryDisposition,
    EventContext,
    EventDelivery,
    OrderingRequirement,
    OwnershipScope,
    ReplayPolicy,
    SubscriptionSpec,
    VersionRange,
)
from agentos.events.in_memory import InMemoryEventBus


@dataclass
class RecordingConsumer:
    dispositions: list[DeliveryDisposition] = field(default_factory=list)
    deliveries: list[EventDelivery] = field(default_factory=list)

    def handle(self, delivery: EventDelivery) -> DeliveryDisposition:
        self.deliveries.append(delivery)
        if self.dispositions:
            return self.dispositions.pop(0)
        return DeliveryDisposition.ACKNOWLEDGED


def _spec(name: str, *, ordering=OrderingRequirement.NONE) -> SubscriptionSpec:
    return SubscriptionSpec(
        consumer_name=name,
        accepted_event_types=("Execution*",),
        accepted_versions=(VersionRange(1, 1),),
        ownership_scope=OwnershipScope("user:1", "workspace:1"),
        ordering_requirement=ordering,
        data_clearance=DataClassification.INTERNAL,
        replay_policy=ReplayPolicy.ALLOWED,
    )


def test_bus_retries_with_same_event_id_and_new_delivery_id(event_factory):
    bus = InMemoryEventBus()
    consumer = RecordingConsumer([DeliveryDisposition.RETRYABLE_FAILURE, DeliveryDisposition.ACKNOWLEDGED])
    bus.subscribe(_spec("retry"), consumer)
    bus.publish((event_factory(),))
    first = bus.deliver_pending()
    second = bus.deliver_pending()
    assert first.attempts[0].event.event_id == second.attempts[0].event.event_id
    assert first.attempts[0].delivery_id != second.attempts[0].delivery_id
    assert second.acknowledged == ("subscription:1",)


def test_ack_deduplicates_by_consumer_and_failure_isolated(event_factory):
    bus = InMemoryEventBus()
    acknowledged = RecordingConsumer()
    permanently_failed = RecordingConsumer([DeliveryDisposition.PERMANENT_FAILURE])
    bus.subscribe(_spec("ack"), acknowledged)
    bus.subscribe(_spec("bad"), permanently_failed)
    bus.publish((event_factory(),))
    result = bus.deliver_pending()
    duplicate = bus.publish((event_factory(),))
    bus.deliver_pending()
    assert result.acknowledged == ("subscription:1",)
    assert len(acknowledged.deliveries) == 1
    assert duplicate.duplicate_event_ids == ("event:1",)
    assert len(bus.quarantine) == 1


def test_per_execution_ordering_retains_gap_and_ignores_late_event(event_factory):
    bus = InMemoryEventBus()
    consumer = RecordingConsumer()
    bus.subscribe(_spec("ordered", ordering=OrderingRequirement.PER_EXECUTION), consumer)
    event_two = event_factory(event_id="event:2", sequence=2)
    event_one = event_factory(event_id="event:1", sequence=1)
    bus.publish((event_two, event_one))
    first = bus.deliver_pending()
    assert first.reconciliation[0].missing_sequence == 1
    bus.publish((event_one,))
    second = bus.deliver_pending()
    assert [delivery.event.sequence for delivery in consumer.deliveries] == [1, 2]
    assert second.reconciliation == ()


def test_subscription_filters_type_version_ownership_and_clearance(event_factory):
    bus = InMemoryEventBus()
    consumer = RecordingConsumer()
    bus.subscribe(_spec("filtered"), consumer)
    bus.publish(
        (
            event_factory(event_type="OtherEvent"),
            event_factory(event_id="event:2", event_version=2),
            event_factory(event_id="event:3", user_id="user:2"),
            event_factory(event_id="event:4", classification=DataClassification.RESTRICTED),
        )
    )
    result = bus.deliver_pending()
    assert result.acknowledged == ()
    assert consumer.deliveries == []
    assert len(bus.audit_log) == 4


def test_public_failure_repr_does_not_expose_consumer_detail(event_factory):
    bus = InMemoryEventBus()
    consumer = RecordingConsumer([DeliveryDisposition.PERMANENT_FAILURE])
    bus.subscribe(_spec("bad"), consumer)
    bus.publish((event_factory(),))
    bus.deliver_pending()
    assert "api_key" not in repr(bus.quarantine[0]).lower()


def test_cancel_subscription_prevents_future_delivery(event_factory):
    bus = InMemoryEventBus()
    consumer = RecordingConsumer()
    ref = bus.subscribe(_spec("cancel"), consumer)
    bus.cancel_subscription(ref)
    bus.publish((event_factory(),))
    assert bus.deliver_pending().attempts == ()
