from datetime import UTC, datetime, timedelta

from agentos.scheduler.engine import DurableScheduleEngine
from agentos.scheduler.models import (
    MisfirePolicy,
    OccurrenceState,
    OverlapPolicy,
    Schedule,
    ScheduleRule,
    ScheduleState,
    ScheduleTarget,
    ScheduleType,
)


def test_claim_and_materialize_are_fenced_and_idempotent() -> None:
    now = datetime.now(UTC)
    engine = DurableScheduleEngine(clock=lambda: now)
    schedule = Schedule(
        schedule_id="schedule-1", user_id="user-1", workspace_id="workspace-1", agent_id="agent-1",
        schedule_type=ScheduleType.FUTURE_EXECUTION, target=ScheduleTarget.task("task:1"),
        rule=ScheduleRule.at(now), timezone="UTC", state=ScheduleState.ACTIVE, version=1,
        starts_at=now, next_fire_at=now, misfire_policy=MisfirePolicy.fire_once_now(),
        overlap_policy=OverlapPolicy.forbid_overlap(),
    )
    engine.put(schedule)

    claim = engine.claim_due("worker-1", due_before=now + timedelta(seconds=1), lease_duration=timedelta(minutes=1))[0]
    occurrence = engine.materialize(claim, execution_id="execution-1")

    assert occurrence.state is OccurrenceState.MATERIALIZED
    assert engine.materialize(claim, execution_id="execution-1").execution_id == "execution-1"
    assert engine.dispatch(occurrence.occurrence_id, occurrence.version, claim.fencing_token, "dispatch-1").state is OccurrenceState.DISPATCHED


def test_pause_prevents_new_claims_and_cancel_preserves_materialized_occurrence() -> None:
    now = datetime.now(UTC)
    engine = DurableScheduleEngine(clock=lambda: now)
    schedule = Schedule(
        schedule_id="schedule-2", user_id="user-1", workspace_id=None, agent_id="system",
        schedule_type=ScheduleType.MAINTENANCE, target=ScheduleTarget.maintenance("retention:1"),
        rule=ScheduleRule.fixed_interval(timedelta(minutes=1), now), timezone="UTC", state=ScheduleState.ACTIVE,
        version=1, starts_at=now, next_fire_at=now, misfire_policy=MisfirePolicy.skip_missed(timedelta(seconds=1)),
        overlap_policy=OverlapPolicy.serialize(),
    )
    engine.put(schedule)
    assert engine.pause("schedule-2", 1).state is ScheduleState.PAUSED
    assert engine.claim_due("worker-1", due_before=now + timedelta(minutes=1), lease_duration=timedelta(minutes=1)) == ()
