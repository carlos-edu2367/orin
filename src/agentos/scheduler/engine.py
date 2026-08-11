from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from .models import OccurrenceState, Schedule, ScheduleClaim, ScheduleOccurrence, ScheduleState


class DurableScheduleEngine:
    """Reference state machine; production persistence is supplied by the PostgreSQL store."""
    def __init__(self, *, clock=None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._schedules: dict[str, Schedule] = {}
        self._occurrences: dict[str, ScheduleOccurrence] = {}
        self._next_fence = 0

    def put(self, schedule: Schedule) -> None: self._schedules[schedule.schedule_id] = schedule

    def pause(self, schedule_id: str, expected_version: int) -> Schedule:
        schedule = self._schedules[schedule_id]
        if schedule.version != expected_version: raise ValueError("schedule version conflict")
        if schedule.state is ScheduleState.PAUSED: return schedule
        if schedule.state is not ScheduleState.ACTIVE: raise ValueError("terminal schedules cannot be paused")
        schedule = schedule.with_state(ScheduleState.PAUSED)
        self._schedules[schedule_id] = schedule
        return schedule

    def claim_due(self, worker_id: str, *, due_before: datetime, lease_duration: timedelta) -> tuple[ScheduleClaim, ...]:
        claims: list[ScheduleClaim] = []
        for schedule in sorted(self._schedules.values(), key=lambda value: value.schedule_id):
            if schedule.state is not ScheduleState.ACTIVE or schedule.next_fire_at is None or schedule.next_fire_at > due_before:
                continue
            occurrence_id = f"{schedule.schedule_id}:{schedule.version}:{schedule.next_fire_at.isoformat()}"
            occurrence = self._occurrences.get(occurrence_id)
            if occurrence is None:
                occurrence = ScheduleOccurrence(occurrence_id, schedule.schedule_id, schedule.version, schedule.next_fire_at, OccurrenceState.PLANNED)
            if occurrence.state is not OccurrenceState.PLANNED: continue
            self._next_fence += 1
            occurrence = replace(occurrence, state=OccurrenceState.CLAIMED, claim_id=f"claim:{occurrence_id}:{self._next_fence}", fencing_token=self._next_fence, version=occurrence.version + 1)
            self._occurrences[occurrence_id] = occurrence
            claims.append(ScheduleClaim(occurrence.claim_id, schedule.schedule_id, occurrence_id, occurrence.version, worker_id, occurrence.fencing_token, self._clock() + lease_duration))
        return tuple(claims)

    def materialize(self, claim: ScheduleClaim, *, execution_id: str) -> ScheduleOccurrence:
        occurrence = self._occurrences[claim.occurrence_id]
        if occurrence.state is OccurrenceState.MATERIALIZED and occurrence.execution_id == execution_id: return occurrence
        if occurrence.version != claim.occurrence_version or occurrence.claim_id != claim.claim_id: raise ValueError("occurrence claim is not current")
        occurrence = occurrence.materialized(execution_id, claim.fencing_token)
        self._occurrences[occurrence.occurrence_id] = occurrence
        return occurrence

    def dispatch(self, occurrence_id: str, expected_version: int, fence: int, dispatch_id: str) -> ScheduleOccurrence:
        occurrence = self._occurrences[occurrence_id]
        if occurrence.version != expected_version: raise ValueError("occurrence version conflict")
        occurrence = occurrence.dispatched(dispatch_id, fence)
        self._occurrences[occurrence_id] = occurrence
        return occurrence
