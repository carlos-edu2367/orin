from dataclasses import dataclass, field, replace

from agentos.events import (
    CommitState,
    EventContext,
    InMemoryEventBus,
    OutboxRecord,
    OutboxPosition,
    PublicationLease,
    PublishOutboxBatch,
)
from agentos.events.in_memory import InMemoryOutboxPublisher


@dataclass
class FakeOutboxSource:
    records: tuple[OutboxRecord, ...]
    confirmations: dict[str, bool] = field(default_factory=dict)
    domain_confirmation_count: int = 0

    def read_outbox(self, request):
        return self.records[: request.maximum_events]

    def inspect_commit(self, record, request):
        return self.confirmations.get(record.event.event_id, False)


def _request() -> PublishOutboxBatch:
    return PublishOutboxBatch(
        "publisher:1",
        "partition:1",
        None,
        10,
        PublicationLease("lease:1", "owner:1", 10),
        EventContext("user:1", "workspace:1", "agent:1", "execution:1", "correlation:1", "publish"),
    )


def test_publisher_only_publishes_committed_entries(event_factory):
    record = OutboxRecord(event_factory(), OutboxPosition("1"), CommitState.COMMITTED)
    source = FakeOutboxSource((record,))
    publisher = InMemoryOutboxPublisher(source, InMemoryEventBus())
    result = publisher.publish_pending(_request())
    assert result.published_event_ids == ("event:1",)


def test_unknown_commit_requires_authorized_inspection(event_factory):
    record = OutboxRecord(event_factory(), OutboxPosition("1"), CommitState.UNKNOWN)
    source = FakeOutboxSource((record,), confirmations={})
    bus = InMemoryEventBus()
    result = InMemoryOutboxPublisher(source, bus).publish_pending(_request())
    assert result.pending_count == 1
    assert bus.events == ()


def test_inspected_unknown_commit_can_be_published(event_factory):
    record = OutboxRecord(event_factory(), OutboxPosition("1"), CommitState.UNKNOWN)
    source = FakeOutboxSource((record,), confirmations={"event:1": True})
    bus = InMemoryEventBus()
    result = InMemoryOutboxPublisher(source, bus).publish_pending(_request())
    assert result.published_event_ids == ("event:1",)


def test_unknown_commit_is_inspected_even_without_optional_request_context(event_factory):
    record = OutboxRecord(event_factory(), OutboxPosition("1"), CommitState.UNKNOWN)
    source = FakeOutboxSource(record and (record,), confirmations={"event:1": True})
    request = replace(_request(), context=None)

    result = InMemoryOutboxPublisher(source, InMemoryEventBus()).publish_pending(request)

    assert result.published_event_ids == ("event:1",)


def test_retry_reuses_event_id_and_does_not_confirm_domain(event_factory):
    record = OutboxRecord(event_factory(), OutboxPosition("1"), CommitState.COMMITTED)
    source = FakeOutboxSource((record,))
    bus = InMemoryEventBus()
    publisher = InMemoryOutboxPublisher(source, bus)
    first = publisher.publish_pending(_request())
    second = publisher.publish_pending(_request())
    assert first.published_event_ids == ("event:1",)
    assert second.duplicate_event_ids == ("event:1",)
    assert source.domain_confirmation_count == 0
    assert [event.event_id for event in bus.events] == ["event:1"]


def test_cursor_does_not_skip_pending_entry_before_later_committed_entry(event_factory):
    pending = OutboxRecord(event_factory(), OutboxPosition("1"), CommitState.UNKNOWN)
    committed = OutboxRecord(
        replace(event_factory(), event_id="event:2", sequence=2),
        OutboxPosition("2"),
        CommitState.COMMITTED,
    )
    source = FakeOutboxSource((pending, committed), confirmations={})
    result = InMemoryOutboxPublisher(source, InMemoryEventBus()).publish_pending(_request())

    assert result.pending_event_ids == ("event:1",)
    assert result.next_position is None
