"""Port-driven orchestration for the persisted scheduler state machine."""
from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from .models import ScheduleClaim, ScheduleOccurrence
from .postgres import PostgresScheduleStore


class ScheduledExecutionCreator(Protocol):
    def create_for_occurrence(self, session, occurrence_id: str) -> str: ...


class OccurrenceDispatcher(Protocol):
    def submit_occurrence(self, session, execution_id: str, occurrence_id: str) -> str: ...


class PostgresScheduleEngine:
    """Coordinates only through ports; it never runs the scheduled payload."""
    def __init__(self, store: PostgresScheduleStore, execution_creator: ScheduledExecutionCreator, dispatcher: OccurrenceDispatcher) -> None:
        self._store = store
        self._execution_creator = execution_creator
        self._dispatcher = dispatcher

    def claim_due(self, worker_id: str, *, due_before, lease_duration: timedelta) -> tuple[ScheduleClaim, ...]:
        return self._store.claim_due(worker_id=worker_id, due_before=due_before, lease_duration=lease_duration)

    def materialize(self, claim: ScheduleClaim) -> ScheduleOccurrence:
        # Re-reading the durable state turns a replay of the same claim into an
        # idempotent result before asking the Execution owner for another ID.
        existing = self._store.get_occurrence(claim.occurrence_id)
        if existing.execution_id is not None:
            return existing
        return self._store.materialize_with_execution(claim, self._execution_creator.create_for_occurrence)

    def dispatch(self, occurrence: ScheduleOccurrence) -> ScheduleOccurrence:
        if occurrence.execution_id is None:
            raise ValueError("only a materialized occurrence can be dispatched")
        return self._store.dispatch_with(occurrence, self._dispatcher.submit_occurrence)


__all__ = ["OccurrenceDispatcher", "PostgresScheduleEngine", "ScheduledExecutionCreator"]
