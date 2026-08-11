from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from agentos.persistence.postgres.schema import metadata
from agentos.scheduler.models import MisfirePolicy, OverlapPolicy, Schedule, ScheduleRule, ScheduleState, ScheduleTarget, ScheduleType
from agentos.scheduler.postgres import PostgresScheduleStore
from agentos.scheduler.service import PostgresScheduleEngine


class RecordingExecutionCreator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def create_for_occurrence(self, session, occurrence_id: str) -> str:
        self.calls.append(occurrence_id)
        return "execution-1"


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def submit_occurrence(self, session, execution_id: str, occurrence_id: str) -> str:
        self.calls.append((execution_id, occurrence_id))
        return "dispatch-1"


def test_persistent_schedule_engine_claims_materializes_and_dispatches_idempotently() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    now = datetime.now(UTC)
    store = PostgresScheduleStore(engine)
    store.save(Schedule("schedule-1", "user-1", None, "system", ScheduleType.MAINTENANCE, ScheduleTarget.maintenance("retention:1"), ScheduleRule.at(now), "UTC", ScheduleState.ACTIVE, 1, now, now, MisfirePolicy.fire_once_now(), OverlapPolicy.forbid_overlap()), idempotency_key="create-1")
    executions = RecordingExecutionCreator()
    dispatches = RecordingDispatcher()
    scheduler = PostgresScheduleEngine(store, executions, dispatches)

    claim = scheduler.claim_due("scheduler-1", due_before=now, lease_duration=timedelta(minutes=1))[0]
    # A fresh adapter instance proves the claim/occurrence is not process memory.
    scheduler = PostgresScheduleEngine(PostgresScheduleStore(engine), executions, dispatches)
    occurrence = scheduler.materialize(claim)
    dispatched = scheduler.dispatch(occurrence)

    assert dispatched.state.value == "DISPATCHED"
    assert executions.calls == [claim.occurrence_id]
    assert dispatches.calls == [("execution-1", claim.occurrence_id)]
    assert scheduler.materialize(claim).execution_id == "execution-1"
