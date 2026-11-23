"""Boot recovery of pipeline runs (WS-C7, spike S2 and risk R6).

A worker that died leaves ``running`` rows in the pipeline journal whose turn is long gone. At boot those runs and
their open steps become ``interrupted``, which is the state a resume starts from. A run whose turn is still alive
(running, queued, starting, paused) is not touched: that turn owns it and, if the worker lost it, ``recover_stale``
re-queues it and ``PipelineDriver`` interrupts and resumes the run itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select

from agentos.persistence.postgres.schema import conversation_turns, pipeline_runs

from .store import PipelineStore

_TERMINAL_TURN_STATES = frozenset({"completed", "failed", "cancelled"})
# A run is created before its turn is claimed; one that still has no turn after this long never got one.
NO_TURN_GRACE = timedelta(minutes=10)


def interrupt_orphaned_pipeline_runs(engine: Any, *, now: datetime | None = None) -> int:
    """Move ``running`` runs whose turn ended (or never existed) to ``interrupted``; return how many."""
    moment = now or datetime.now(UTC)
    runs, turns = pipeline_runs, conversation_turns
    statement = (
        select(runs.c.run_id, runs.c.turn_id, runs.c.started_at, turns.c.state)
        .select_from(runs.outerjoin(turns, turns.c.turn_id == runs.c.turn_id))
        .where(and_(runs.c.state == "running"))
    )
    orphans: list[str] = []
    with engine.connect() as connection:
        for run_id, turn_id, started_at, turn_state in connection.execute(statement):
            if turn_id is None:
                started = started_at if started_at.tzinfo else started_at.replace(tzinfo=UTC)
                if moment - started >= NO_TURN_GRACE:
                    orphans.append(run_id)
            elif turn_state is None or turn_state in _TERMINAL_TURN_STATES:
                orphans.append(run_id)
    store = PipelineStore(engine)
    return sum(store.interrupt_orphans(run_id) for run_id in orphans)


__all__ = ["NO_TURN_GRACE", "interrupt_orphaned_pipeline_runs"]
