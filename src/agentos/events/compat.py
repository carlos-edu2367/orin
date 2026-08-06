from __future__ import annotations

from agentos.execution.events import (
    DataClassification as LegacyClassification,
    EventEnvelope as LegacyEventEnvelope,
    OutboxEntry,
)
from agentos.execution.in_memory import InMemoryTransactionalPersistence
from agentos.execution.models import CorrelationId, EventId, ExecutionId, Ownership

from .models import CommitState, DataClassification, EventEnvelope
from .ports import ConfirmedOutboxSource, OutboxPosition, OutboxRecord, PublishOutboxBatch


def to_canonical_event(legacy: LegacyEventEnvelope, *, agent_id: str | None = None) -> EventEnvelope:
    return EventEnvelope(
        event_id=str(legacy.event_id),
        event_type=str(legacy.event_type),
        event_version=legacy.event_version,
        occurred_at=legacy.occurred_at,
        source=legacy.source,
        correlation_id=str(legacy.correlation_id),
        causation_id=legacy.causation_id,
        sequence=legacy.sequence,
        user_id=str(legacy.ownership.user_id),
        workspace_id=(
            str(legacy.ownership.workspace_id) if legacy.ownership.workspace_id is not None else None
        ),
        agent_id=agent_id,
        execution_id=str(legacy.execution_id),
        classification=DataClassification(str(legacy.classification)),
        payload=legacy.payload,
    )


def from_execution_event(canonical: EventEnvelope) -> LegacyEventEnvelope:
    if canonical.execution_id is None or canonical.sequence is None:
        raise ValueError("legacy execution envelope requires execution_id and sequence")
    return LegacyEventEnvelope(
        event_id=EventId(canonical.event_id),
        event_type=canonical.event_type,
        event_version=canonical.event_version,
        occurred_at=canonical.occurred_at,
        source=canonical.source,
        correlation_id=CorrelationId(canonical.correlation_id),
        causation_id=canonical.causation_id,
        sequence=canonical.sequence,
        ownership=Ownership(canonical.user_id, canonical.workspace_id),
        execution_id=ExecutionId(canonical.execution_id),
        classification=LegacyClassification(canonical.classification.value),
        payload=canonical.payload,
    )


class InMemoryTransactionalOutboxSource(ConfirmedOutboxSource):
    """Read-only adapter exposing committed legacy outbox entries canonically."""

    def __init__(self, persistence: InMemoryTransactionalPersistence) -> None:
        self._persistence = persistence

    def read_outbox(self, request: PublishOutboxBatch) -> tuple[OutboxRecord, ...]:
        records: list[OutboxRecord] = []
        after = -1
        if request.after_position is not None:
            try:
                after = int(request.after_position.value)
            except ValueError as exc:
                raise ValueError("invalid outbox position") from exc
        for index, entry in enumerate(self._persistence.confirmed_outbox()):
            if index <= after:
                continue
            records.append(
                OutboxRecord(
                    event=to_canonical_event(entry.event),
                    position=OutboxPosition(str(index)),
                    commit_state=CommitState.COMMITTED,
                )
            )
            if len(records) >= request.maximum_events:
                break
        return tuple(records)

    def inspect_commit(self, record: OutboxRecord, request: PublishOutboxBatch) -> bool:
        return record.commit_state is CommitState.COMMITTED
