from __future__ import annotations

from typing import Protocol

from .models import Schedule, ScheduleClaim, ScheduleOccurrence


class ScheduleStore(Protocol):
    def save(self, schedule: Schedule, *, idempotency_key: str) -> Schedule: ...
    def claim_due(self, *, worker_id: str, due_before, lease_duration) -> tuple[ScheduleClaim, ...]: ...
    def materialize(self, claim: ScheduleClaim, *, execution_id: str) -> ScheduleOccurrence: ...
