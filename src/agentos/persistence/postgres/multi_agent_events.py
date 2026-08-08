"""Durable ``MultiAgentEventRecorder`` backed by PostgreSQL (frontend Fase B.1).

Mirrors ``agentos.multi_agent.in_memory.InMemoryMultiAgentStore.record_event``
semantics exactly (idempotent on ``event_id``: a duplicate identical event
returns ``False``, a duplicate with different content raises), but persists
rows in ``multi_agent_events`` instead of a process-local dict, so a restart
or a different process handling a later request observes the same facts.

There is deliberately no FK to ``persistence_records`` here: a delegation or
message fact is not a versioned "record" of an execution the way
``persistence_outbox.source_record_ref`` is (see docs/frontend/
PROJECT_CLOSEOUT_ROADMAP.md, Fase B.1).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from agentos.events import EventEnvelope

from .schema import multi_agent_events


def _matches(row, event: EventEnvelope) -> bool:
    stored_event = row["event"]
    return (
        row["event_type"] == event.event_type
        and row["user_id"] == event.user_id
        and row["workspace_id"] == event.workspace_id
        and row["agent_id"] == event.agent_id
        and row["execution_id"] == event.execution_id
        and row["correlation_id"] == event.correlation_id
        and row["causation_id"] == event.causation_id
        and row["sequence"] == event.sequence
        and row["classification"] == event.classification.value
        and stored_event["occurred_at"] == event.occurred_at.isoformat()
        and stored_event["payload"] == dict(event.payload)
    )


class PostgresMultiAgentEventRecorder:
    """Production adapter for the ``MultiAgentEventRecorder`` port (frontend Fase B.1)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record_event(self, event: EventEnvelope) -> bool:
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(multi_agent_events).where(multi_agent_events.c.event_id == event.event_id)
            ).mappings().first()
            if existing is not None:
                if not _matches(existing, event):
                    raise ValueError("event_id already exists with a different event")
                return False
            connection.execute(
                insert(multi_agent_events).values(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    user_id=event.user_id,
                    workspace_id=event.workspace_id,
                    agent_id=event.agent_id,
                    execution_id=event.execution_id,
                    correlation_id=event.correlation_id,
                    causation_id=event.causation_id,
                    sequence=event.sequence,
                    classification=event.classification.value,
                    event={
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "sequence": event.sequence,
                        "occurred_at": event.occurred_at.isoformat(),
                        "payload": dict(event.payload),
                    },
                    created_at=datetime.now(UTC),
                )
            )
            return True


__all__ = ["PostgresMultiAgentEventRecorder"]
