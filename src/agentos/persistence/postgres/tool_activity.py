"""Durable sink for ``tool_runtime.ToolOutboxEntry`` (frontend Fase B.2).

``ToolRuntimeService.outbox`` is (and stays) an in-process list; this sink is
the optional, additive durability path (``ToolRuntimeService(..., sink=...)``,
see ``agentos.tool_runtime.runtime``) that writes every entry to
``tool_activity_events`` so a Tool fact survives past the process that ran
the invocation. There is deliberately no idempotency dedup here: the
in-memory outbox it mirrors has none either (each ``_entry()`` call is one
``list.append``), so a duplicate write is not a contract this sink needs to
prevent — ``event_id`` only needs to be unique per row, not stable across
retries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert
from sqlalchemy.engine import Engine

from agentos.tool_runtime.models import ToolOutboxEntry

from .schema import tool_activity_events


class PostgresToolActivitySink:
    """Production sink adapter satisfying ``Callable[[ToolOutboxEntry], None]``."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(self, entry: ToolOutboxEntry) -> None:
        context = entry.context
        payload = dict(entry.payload)
        payload.setdefault("invocation_id", entry.invocation_id)
        payload.setdefault("tool_kind", entry.tool_ref.tool_id)
        with self._engine.begin() as connection:
            connection.execute(
                insert(tool_activity_events).values(
                    event_id=f"tool:{entry.invocation_id}:{entry.event_type}:{uuid4().hex}",
                    event_type=entry.event_type,
                    user_id=context.user_id,
                    workspace_id=context.workspace_id,
                    agent_id=context.agent_id,
                    execution_id=context.execution_id,
                    correlation_id=entry.correlation_id,
                    invocation_id=entry.invocation_id,
                    event={
                        "event_type": entry.event_type,
                        "occurred_at": entry.occurred_at.isoformat(),
                        "payload": payload,
                    },
                    created_at=datetime.now(UTC),
                )
            )


__all__ = ["PostgresToolActivitySink"]
