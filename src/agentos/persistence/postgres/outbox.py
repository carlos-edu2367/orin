from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from agentos.events.models import DataClassification, EventEnvelope
from agentos.events.ports import (
    CommitState as EventCommitState,
    ConfirmedOutboxSource,
    OutboxPosition,
    OutboxRecord,
    PublishOutboxBatch,
)

from .schema import persistence_outbox


class PostgresConfirmedOutboxSource(ConfirmedOutboxSource):
    """Read-only bridge from committed PostgreSQL outbox rows to Events."""

    def __init__(self, persistence) -> None:
        if not hasattr(persistence, "_Session"):
            raise TypeError("persistence must be a PostgreSQL persistence adapter")
        self._Session = persistence._Session

    def read_outbox(self, request: PublishOutboxBatch) -> tuple[OutboxRecord, ...]:
        if request.context is None:
            return ()
        after = self._after_position(request.after_position)
        context = request.context
        workspace = (
            persistence_outbox.c.workspace_id.is_(None)
            if context.workspace_id is None
            else persistence_outbox.c.workspace_id == context.workspace_id
        )
        statement = (
            select(persistence_outbox)
            .where(
                persistence_outbox.c.id > after,
                persistence_outbox.c.published_at.is_(None),
                persistence_outbox.c.user_id == context.user_id,
                workspace,
                persistence_outbox.c.agent_id == context.agent_id,
                persistence_outbox.c.execution_id == context.execution_id,
                persistence_outbox.c.correlation_id == context.correlation_id,
                persistence_outbox.c.purpose == context.purpose,
            )
            .order_by(persistence_outbox.c.id)
            .limit(request.maximum_events)
        )
        with self._Session() as session:
            rows = session.execute(statement).mappings().all()
        return tuple(self._record(row) for row in rows)

    def inspect_commit(self, record: OutboxRecord, request: PublishOutboxBatch) -> bool:
        if request.context is None:
            return False
        context = request.context
        workspace = (
            persistence_outbox.c.workspace_id.is_(None)
            if context.workspace_id is None
            else persistence_outbox.c.workspace_id == context.workspace_id
        )
        statement = select(persistence_outbox.c.event_id).where(
            persistence_outbox.c.event_id == record.event.event_id,
            persistence_outbox.c.user_id == context.user_id,
            workspace,
            persistence_outbox.c.agent_id == context.agent_id,
            persistence_outbox.c.execution_id == context.execution_id,
            persistence_outbox.c.correlation_id == context.correlation_id,
            persistence_outbox.c.purpose == context.purpose,
        )
        with self._Session() as session:
            return session.execute(statement).scalar_one_or_none() is not None

    @staticmethod
    def _after_position(position: OutboxPosition | None) -> int:
        if position is None:
            return 0
        try:
            return int(position.value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid outbox position") from exc

    @staticmethod
    def _record(row) -> OutboxRecord:
        event = row["event"]
        return OutboxRecord(
            event=EventEnvelope(
                event_id=str(event["event_id"]),
                event_type=str(event["event_type"]),
                event_version=int(event["event_version"]),
                occurred_at=datetime.fromisoformat(str(event["occurred_at"])),
                source=str(event["source"]),
                correlation_id=str(event["correlation_id"]),
                causation_id=event.get("causation_id"),
                sequence=event.get("sequence"),
                user_id=str(event["user_id"]),
                workspace_id=event.get("workspace_id"),
                execution_id=event.get("execution_id"),
                classification=DataClassification(str(event["classification"])),
                payload=event["payload"],
                agent_id=event.get("agent_id"),
            ),
            position=OutboxPosition(str(row["id"])),
            commit_state=EventCommitState.COMMITTED,
            transaction_id=str(row["transaction_id"]),
        )


__all__ = ["PostgresConfirmedOutboxSource"]
