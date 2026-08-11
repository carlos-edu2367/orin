"""Explicit PostgreSQL persistence for schedules and occurrence claims.

This adapter assumes migrations have already been applied by an administrative
operation. It contains no ``create_all`` or implicit migration path.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from agentos.persistence.postgres.schema import schedule_occurrences, schedules

from .models import OccurrenceState, Schedule, ScheduleClaim, ScheduleOccurrence, ScheduleState


class ScheduleConflictError(RuntimeError):
    ...


class PostgresScheduleStore:
    def __init__(self, engine: Engine) -> None:
        self._Session = sessionmaker(bind=engine, future=True)

    def save(self, schedule: Schedule, *, idempotency_key: str) -> Schedule:
        now = datetime.now(UTC)
        target = {"kind": schedule.target.kind.value, "immutable_ref": schedule.target.immutable_ref, "destination_pool": schedule.target.destination_pool.value}
        rule = {"fire_at": schedule.rule.fire_at.isoformat() if schedule.rule.fire_at else None, "interval_seconds": schedule.rule.interval.total_seconds() if schedule.rule.interval else None, "anchor_at": schedule.rule.anchor_at.isoformat() if schedule.rule.anchor_at else None}
        policies = {
            "misfire": {"kind": schedule.misfire_policy.kind, "grace_seconds": schedule.misfire_policy.grace.total_seconds(), "max_occurrences": schedule.misfire_policy.max_occurrences},
            "overlap": {"kind": schedule.overlap_policy.kind, "max_active": schedule.overlap_policy.max_active},
        }
        values = dict(user_id=schedule.user_id, workspace_id=schedule.workspace_id, agent_id=schedule.agent_id, schedule_type=schedule.schedule_type.value, target=target, rule=rule, timezone=schedule.timezone, policies=policies, state=schedule.state.value, version=schedule.version, next_fire_at=schedule.next_fire_at, starts_at=schedule.starts_at, ends_at=schedule.ends_at, idempotency_key=idempotency_key, updated_at=now)
        with self._Session.begin() as session:
            existing = session.execute(select(schedules.c.id).where(schedules.c.schedule_id == schedule.schedule_id)).scalar_one_or_none()
            if existing is None:
                session.execute(schedules.insert().values(schedule_id=schedule.schedule_id, created_at=now, **values))
            else:
                session.execute(update(schedules).where(schedules.c.schedule_id == schedule.schedule_id, schedules.c.version < schedule.version).values(**values))
        return schedule

    def due(self, *, due_before: datetime) -> tuple[Schedule, ...]:
        """Expose durable candidates; an engine applies policy before claiming."""
        with self._Session() as session:
            return tuple(self._schedule(row) for row in session.execute(select(schedules).where(schedules.c.state == ScheduleState.ACTIVE.value, schedules.c.next_fire_at <= due_before)).mappings())

    def claim_due(self, *, worker_id: str, due_before: datetime, lease_duration: timedelta) -> tuple[ScheduleClaim, ...]:
        """Persist claims using a monotonically increasing per-occurrence fence."""
        now = datetime.now(UTC)
        claims: list[ScheduleClaim] = []
        with self._Session.begin() as session:
            rows = session.execute(
                select(schedules).where(schedules.c.state == ScheduleState.ACTIVE.value, schedules.c.next_fire_at <= due_before).with_for_update()
            ).mappings()
            for schedule_row in rows:
                schedule = self._schedule(schedule_row)
                logical_at = schedule.next_fire_at
                if logical_at is None:
                    continue
                occurrence_id = f"{schedule.schedule_id}:{schedule.version}:{logical_at.isoformat()}"
                occurrence = session.execute(select(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == occurrence_id).with_for_update()).mappings().one_or_none()
                if occurrence is not None and occurrence["state"] not in {OccurrenceState.PLANNED.value, OccurrenceState.CLAIMED.value}:
                    continue
                if occurrence is not None and occurrence["state"] == OccurrenceState.CLAIMED.value and occurrence["claim_expires_at"] and _aware(occurrence["claim_expires_at"]) > now:
                    continue
                previous_fence = int(occurrence["state_fencing_token"]) if occurrence is not None else 0
                previous_version = int(occurrence["version"]) if occurrence is not None else 0
                fence = previous_fence + 1
                claim_id = f"claim:{occurrence_id}:{fence}"
                values = dict(state=OccurrenceState.CLAIMED.value, version=previous_version + 1, state_fencing_token=fence, claim_id=claim_id, claim_owner=worker_id, claim_expires_at=now + lease_duration, updated_at=now)
                if occurrence is None:
                    session.execute(schedule_occurrences.insert().values(occurrence_id=occurrence_id, schedule_id=schedule.schedule_id, schedule_version=schedule.version, logical_scheduled_at=logical_at, execution_id=None, dispatch_id=None, dispatch_attempt_count=0, reason_code=None, created_at=now, **values))
                else:
                    session.execute(update(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == occurrence_id, schedule_occurrences.c.version == previous_version, schedule_occurrences.c.state_fencing_token == previous_fence).values(**values))
                claims.append(ScheduleClaim(claim_id, schedule.schedule_id, occurrence_id, previous_version + 1, worker_id, fence, now + lease_duration))
        return tuple(claims)

    def materialize(self, claim: ScheduleClaim, *, execution_id: str) -> ScheduleOccurrence:
        now = datetime.now(UTC)
        with self._Session.begin() as session:
            existing = session.execute(select(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == claim.occurrence_id).with_for_update()).mappings().one()
            if existing["state"] in {OccurrenceState.MATERIALIZED.value, OccurrenceState.DISPATCHED.value} and existing["execution_id"]:
                return self._occurrence(existing)
            result = session.execute(update(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == claim.occurrence_id, schedule_occurrences.c.state == OccurrenceState.CLAIMED.value, schedule_occurrences.c.version == claim.occurrence_version, schedule_occurrences.c.claim_id == claim.claim_id, schedule_occurrences.c.state_fencing_token == claim.fencing_token).values(state=OccurrenceState.MATERIALIZED.value, execution_id=execution_id, claim_id=None, claim_owner=None, claim_expires_at=None, version=claim.occurrence_version + 1, updated_at=now))
            if result.rowcount != 1:
                raise ScheduleConflictError("schedule claim, version, or fencing token is no longer current")
            row = session.execute(select(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == claim.occurrence_id)).mappings().one()
        return self._occurrence(row)

    def materialize_with_execution(self, claim: ScheduleClaim, create_execution) -> ScheduleOccurrence:
        """Atomically persist occurrence materialization with the Execution owner."""
        now = datetime.now(UTC)
        with self._Session.begin() as session:
            existing = session.execute(select(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == claim.occurrence_id).with_for_update()).mappings().one()
            if existing["state"] in {OccurrenceState.MATERIALIZED.value, OccurrenceState.DISPATCHED.value} and existing["execution_id"]:
                return self._occurrence(existing)
            execution_id = create_execution(session, claim.occurrence_id)
            result = session.execute(update(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == claim.occurrence_id, schedule_occurrences.c.state == OccurrenceState.CLAIMED.value, schedule_occurrences.c.version == claim.occurrence_version, schedule_occurrences.c.claim_id == claim.claim_id, schedule_occurrences.c.state_fencing_token == claim.fencing_token).values(state=OccurrenceState.MATERIALIZED.value, execution_id=execution_id, claim_id=None, claim_owner=None, claim_expires_at=None, version=claim.occurrence_version + 1, updated_at=now))
            if result.rowcount != 1:
                raise ScheduleConflictError("schedule claim, version, or fencing token is no longer current")
            row = session.execute(select(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == claim.occurrence_id)).mappings().one()
        return self._occurrence(row)

    def get_occurrence(self, occurrence_id: str) -> ScheduleOccurrence:
        with self._Session() as session:
            row = session.execute(select(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == occurrence_id)).mappings().one()
        return self._occurrence(row)

    def mark_dispatched(self, occurrence: ScheduleOccurrence, *, dispatch_id: str) -> ScheduleOccurrence:
        now = datetime.now(UTC)
        with self._Session.begin() as session:
            row = session.execute(select(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == occurrence.occurrence_id).with_for_update()).mappings().one()
            if row["state"] == OccurrenceState.DISPATCHED.value and row["dispatch_id"] == dispatch_id:
                return self._occurrence(row)
            result = session.execute(update(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == occurrence.occurrence_id, schedule_occurrences.c.state == OccurrenceState.MATERIALIZED.value, schedule_occurrences.c.version == occurrence.version, schedule_occurrences.c.state_fencing_token == occurrence.fencing_token).values(state=OccurrenceState.DISPATCHED.value, dispatch_id=dispatch_id, version=occurrence.version + 1, updated_at=now))
            if result.rowcount != 1:
                raise ScheduleConflictError("occurrence version or fencing token is no longer current")
            row = session.execute(select(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == occurrence.occurrence_id)).mappings().one()
        return self._occurrence(row)

    def dispatch_with(self, occurrence: ScheduleOccurrence, submit_dispatch) -> ScheduleOccurrence:
        """Atomically record the durable dispatch decision with the occurrence."""
        now = datetime.now(UTC)
        with self._Session.begin() as session:
            row = session.execute(select(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == occurrence.occurrence_id).with_for_update()).mappings().one()
            if row["state"] == OccurrenceState.DISPATCHED.value and row["dispatch_id"]:
                return self._occurrence(row)
            if row["state"] != OccurrenceState.MATERIALIZED.value or row["version"] != occurrence.version or row["state_fencing_token"] != occurrence.fencing_token:
                raise ScheduleConflictError("occurrence version or fencing token is no longer current")
            dispatch_id = submit_dispatch(session, occurrence.execution_id, occurrence.occurrence_id)
            result = session.execute(update(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == occurrence.occurrence_id, schedule_occurrences.c.state == OccurrenceState.MATERIALIZED.value, schedule_occurrences.c.version == occurrence.version, schedule_occurrences.c.state_fencing_token == occurrence.fencing_token).values(state=OccurrenceState.DISPATCHED.value, dispatch_id=dispatch_id, version=occurrence.version + 1, updated_at=now))
            if result.rowcount != 1:
                raise ScheduleConflictError("occurrence changed while dispatching")
            row = session.execute(select(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == occurrence.occurrence_id)).mappings().one()
        return self._occurrence(row)

    def _schedule(self, row) -> Schedule:
        from datetime import timedelta
        from .models import MisfirePolicy, OverlapPolicy, ScheduleRule, ScheduleTarget, ScheduleType, TargetKind
        target = row["target"]
        rule_data = row["rule"]
        fire_at = datetime.fromisoformat(rule_data["fire_at"]) if rule_data["fire_at"] else None
        rule = ScheduleRule.at(fire_at) if fire_at else ScheduleRule.fixed_interval(timedelta(seconds=rule_data["interval_seconds"]), datetime.fromisoformat(rule_data["anchor_at"]))
        policies = row["policies"]
        misfire = policies["misfire"]
        return Schedule(row["schedule_id"], row["user_id"], row["workspace_id"], row["agent_id"], ScheduleType(row["schedule_type"]), ScheduleTarget(TargetKind(target["kind"]), target["immutable_ref"], target["destination_pool"]), rule, row["timezone"], ScheduleState(row["state"]), row["version"], _aware(row["starts_at"]), _aware(row["next_fire_at"]) if row["next_fire_at"] else None, MisfirePolicy(misfire["kind"], timedelta(seconds=misfire["grace_seconds"]), misfire["max_occurrences"]), OverlapPolicy(**policies["overlap"]), _aware(row["ends_at"]) if row["ends_at"] else None)

    @staticmethod
    def _occurrence(row) -> ScheduleOccurrence:
        return ScheduleOccurrence(row["occurrence_id"], row["schedule_id"], row["schedule_version"], _aware(row["logical_scheduled_at"]), OccurrenceState(row["state"]), row["version"], row["state_fencing_token"], row["execution_id"], row["dispatch_id"], row["claim_id"])


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None and value.utcoffset() is not None else value.replace(tzinfo=UTC)


__all__ = ["PostgresScheduleStore", "ScheduleConflictError"]
