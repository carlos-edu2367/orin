from datetime import datetime, timezone

from agentos.events.compat import from_execution_event, to_canonical_event
from agentos.execution.events import DataClassification, EventEnvelope
from agentos.execution.models import ExecutionId, EventId, CorrelationId, Ownership
from agentos.execution.in_memory import InMemoryTransactionalPersistence


def _legacy() -> EventEnvelope:
    return EventEnvelope(
        event_id=EventId("event:1"),
        event_type="ExecutionStarted",
        event_version=1,
        occurred_at=datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
        source="execution",
        correlation_id=CorrelationId("correlation:1"),
        causation_id="command:1",
        sequence=1,
        ownership=Ownership("user:1", "workspace:1"),
        execution_id=ExecutionId("execution:1"),
        classification=DataClassification.INTERNAL,
        payload={"state_version": 2},
    )


def test_legacy_execution_envelope_round_trips_without_losing_identity():
    legacy = _legacy()
    canonical = to_canonical_event(legacy, agent_id="agent:1")
    restored = from_execution_event(canonical)
    assert restored.event_id == legacy.event_id
    assert restored.sequence == legacy.sequence
    assert restored.payload == legacy.payload
    assert restored.execution_id == legacy.execution_id


def test_legacy_event_without_execution_remains_without_sequence():
    legacy = _legacy()
    canonical = to_canonical_event(legacy, agent_id="agent:1")
    assert canonical.sequence == 1


def test_execution_persistence_exposes_only_confirmed_outbox():
    assert InMemoryTransactionalPersistence().confirmed_outbox() == ()
