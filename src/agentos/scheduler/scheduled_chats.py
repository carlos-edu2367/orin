"""Durable scheduled-chat application service.

The scheduler owns time and occurrence state; the existing chat publisher and
worker remain the only code that executes a model turn.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import (
    provider_configurations, provider_model_catalog,
    projects, schedule_occurrences, scheduled_chat_tasks, schedules,
)


RULES = frozenset({"once", "hourly", "daily", "weekly"})


@dataclass(frozen=True, slots=True)
class ScheduledChatInput:
    message: str
    provider: str
    model_id: str
    timezone: str
    recurrence: str
    fire_at: datetime | None = None
    time_of_day: str | None = None
    weekday: int | None = None
    project_id: str | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("fire_at must include a timezone")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    """SQLite returns naive timestamps although the production column is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None or value.utcoffset() is None else value.astimezone(UTC)


def _clock_now() -> datetime:
    return datetime.now(UTC)


class ScheduledChatService:
    """Creates and materializes user-owned scheduled chat turns."""

    def __init__(self, engine: Engine, *, clock=_clock_now) -> None:
        self._engine = engine
        self._chat = PostgresChatStore(engine)
        self._clock = clock

    def heartbeat(self, component: str) -> None:
        """Record that the poller has a usable durable-store connection."""
        self._chat.heartbeat(component)

    @staticmethod
    def _zone(name: str) -> ZoneInfo:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must be an IANA timezone") from error

    @staticmethod
    def _time(value: str | None) -> tuple[int, int]:
        if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
            raise ValueError("time_of_day must use HH:MM")
        try:
            hour, minute = int(value[:2]), int(value[3:])
        except ValueError as error:
            raise ValueError("time_of_day must use HH:MM") from error
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("time_of_day must use HH:MM")
        return hour, minute

    def next_fire(self, request: ScheduledChatInput, *, after: datetime | None = None) -> datetime:
        """Calculate the first strict future local-calendar occurrence in UTC."""
        now = _utc(after or self._clock())
        if request.recurrence not in RULES:
            raise ValueError("unsupported recurrence")
        zone = self._zone(request.timezone)
        if request.recurrence == "once":
            if request.fire_at is None:
                raise ValueError("once schedules require fire_at")
            fire_at = _utc(request.fire_at)
            if fire_at <= now:
                raise ValueError("fire_at must be in the future")
            return fire_at
        if request.recurrence == "hourly":
            # The first occurrence is exactly one hour after creation; later
            # values are anchored to the previous logical occurrence.
            return now + timedelta(hours=1)
        hour, minute = self._time(request.time_of_day)
        local_now = now.astimezone(zone)
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if request.recurrence == "weekly":
            if request.weekday is None or not 0 <= request.weekday <= 6:
                raise ValueError("weekly schedules require weekday from 0 to 6")
            candidate += timedelta(days=(request.weekday - candidate.weekday()) % 7)
        if candidate <= local_now:
            candidate += timedelta(days=7 if request.recurrence == "weekly" else 1)
        return candidate.astimezone(UTC)

    def create(self, user_id: str, request: ScheduledChatInput, *, idempotency_key: str) -> dict[str, object]:
        message = " ".join(request.message.split())
        if not message or len(message) > 16000:
            raise ValueError("message must be a bounded non-blank string")
        if not request.provider.strip() or not request.model_id.strip():
            raise ValueError("provider and model_id are required")
        next_fire = self.next_fire(request)
        now = self._clock()
        schedule_id, task_id = f"schedule_{uuid4().hex}", f"scheduled_chat_{uuid4().hex}"
        rule = self._rule(request, next_fire)
        with self._engine.begin() as connection:
            existing = connection.execute(select(schedules).where(schedules.c.user_id == user_id, schedules.c.idempotency_key == idempotency_key)).mappings().first()
            if existing is not None:
                return self._public(existing)
            if request.project_id:
                project = connection.execute(select(projects.c.workspace_id).where(
                    projects.c.project_id == request.project_id,
                    projects.c.user_id == user_id,
                    projects.c.archived_at.is_(None),
                )).scalar_one_or_none()
                if project is None:
                    raise LookupError("project not found")
                workspace_id = str(project)
            else:
                workspace_id = None
            # Do not allow a stale or forged UI model selection to persist.
            model = connection.execute(select(provider_model_catalog.c.id).where(
                provider_model_catalog.c.user_id == user_id,
                provider_model_catalog.c.provider == request.provider,
                provider_model_catalog.c.model_id == request.model_id,
            )).scalar_one_or_none()
            if model is None:
                raise ValueError("model is not authorized")
            provider = connection.execute(select(provider_configurations.c.user_id).where(
                provider_configurations.c.user_id == user_id,
                provider_configurations.c.provider == request.provider,
                provider_configurations.c.enabled.is_(True),
            )).scalar_one_or_none()
            if provider is None:
                raise ValueError("provider is not enabled")
            connection.execute(insert(scheduled_chat_tasks).values(
                task_id=task_id, schedule_id=schedule_id, user_id=user_id, message=message,
                provider=request.provider, model_id=request.model_id, project_id=request.project_id,
                conversation_id=None, created_at=now, updated_at=now,
            ))
            connection.execute(insert(schedules).values(
                schedule_id=schedule_id, user_id=user_id, workspace_id=workspace_id,
                agent_id=f"agent:schedule:{schedule_id}", schedule_type="FUTURE_EXECUTION",
                target={"kind": "TASK", "immutable_ref": task_id, "destination_pool": "AGENT"},
                rule=rule, timezone=request.timezone,
                policies={"misfire": {"kind": "FIRE_ONCE_NOW", "grace_seconds": 0, "max_occurrences": 1}, "overlap": {"kind": "SERIALIZE", "max_active": 1}},
                state="ACTIVE", version=1, next_fire_at=next_fire, starts_at=now, ends_at=None,
                idempotency_key=idempotency_key, created_at=now, updated_at=now,
            ))
        return {"schedule_id": schedule_id, "state": "ACTIVE", "next_fire_at": next_fire.isoformat(), "recurrence": request.recurrence}

    @staticmethod
    def _rule(request: ScheduledChatInput, next_fire: datetime) -> dict[str, object]:
        return {
            "kind": request.recurrence, "fire_at": request.fire_at.astimezone(UTC).isoformat() if request.fire_at else None,
            "time_of_day": request.time_of_day, "weekday": request.weekday,
            "anchor_at": next_fire.isoformat(),
        }

    @staticmethod
    def _public(row) -> dict[str, object]:
        rule = dict(row["rule"] or {})
        return {"schedule_id": str(row["schedule_id"]), "state": str(row["state"]), "next_fire_at": row["next_fire_at"].isoformat() if row["next_fire_at"] else None, "recurrence": str(rule.get("kind") or "once"), "project_id": None}

    def list(self, user_id: str) -> dict[str, object]:
        with self._engine.connect() as connection:
            rows = connection.execute(select(schedules, scheduled_chat_tasks.c.project_id, scheduled_chat_tasks.c.message, scheduled_chat_tasks.c.provider, scheduled_chat_tasks.c.model_id, scheduled_chat_tasks.c.conversation_id)
                .join(scheduled_chat_tasks, scheduled_chat_tasks.c.schedule_id == schedules.c.schedule_id)
                .where(schedules.c.user_id == user_id).order_by(schedules.c.created_at.desc())).mappings().all()
        return {"items": [{**self._public(row), "project_id": row["project_id"], "message": row["message"], "provider": row["provider"], "model_id": row["model_id"], "conversation_id": row["conversation_id"]} for row in rows]}

    def cancel(self, user_id: str, schedule_id: str) -> bool:
        now = self._clock()
        with self._engine.begin() as connection:
            result = connection.execute(update(schedules).where(
                schedules.c.schedule_id == schedule_id, schedules.c.user_id == user_id,
                schedules.c.state.in_(("ACTIVE", "PAUSED")),
            ).values(state="CANCELLED", version=schedules.c.version + 1, next_fire_at=None, updated_at=now))
        return bool(result.rowcount)

    def run_due(self, *, worker_id: str, due_before: datetime | None = None) -> tuple[str, ...]:
        """Claim due occurrences, create one normal chat turn, and advance safely."""
        due = _utc(due_before or self._clock())
        claimed: list[tuple[str, str]] = []
        with self._engine.begin() as connection:
            # A process may die after creating the chat turn but before writing
            # the occurrence receipt. Replaying the same occurrence is safe:
            # the chat-store idempotency key is derived from occurrence_id.
            expired = connection.execute(select(schedule_occurrences.c.schedule_id, schedule_occurrences.c.occurrence_id).where(
                schedule_occurrences.c.state == "CLAIMED",
                schedule_occurrences.c.claim_expires_at <= due,
            )).all()
            claimed.extend((str(row.schedule_id), str(row.occurrence_id)) for row in expired)
            rows = connection.execute(select(schedules).where(schedules.c.state == "ACTIVE", schedules.c.next_fire_at <= due).with_for_update()).mappings().all()
            for schedule in rows:
                active = connection.execute(select(schedule_occurrences.c.occurrence_id).where(
                    schedule_occurrences.c.schedule_id == schedule["schedule_id"],
                    schedule_occurrences.c.state.in_(("CLAIMED", "MATERIALIZED")),
                )).scalar_one_or_none()
                if active is not None:
                    continue
                logical_at = _stored_utc(schedule["next_fire_at"])
                occurrence_id = f"{schedule['schedule_id']}:{schedule['version']}:{logical_at.isoformat()}"
                existing = connection.execute(select(schedule_occurrences.c.occurrence_id).where(schedule_occurrences.c.occurrence_id == occurrence_id)).scalar_one_or_none()
                if existing is not None:
                    continue
                fence, now = 1, self._clock()
                connection.execute(insert(schedule_occurrences).values(
                    occurrence_id=occurrence_id, schedule_id=schedule["schedule_id"], schedule_version=schedule["version"], logical_scheduled_at=logical_at,
                    state="CLAIMED", version=1, state_fencing_token=fence, claim_id=f"claim:{occurrence_id}:{fence}", claim_owner=worker_id,
                    claim_expires_at=now + timedelta(minutes=5), execution_id=None, dispatch_id=None, dispatch_attempt_count=0, reason_code=None, created_at=now, updated_at=now,
                ))
                rule = dict(schedule["rule"] or {})
                next_at = self._next_after_rule(rule, str(schedule["timezone"]), logical_at)
                values = {"next_fire_at": next_at, "updated_at": now}
                if next_at is None:
                    values["state"] = "EXPIRED"
                connection.execute(update(schedules).where(schedules.c.schedule_id == schedule["schedule_id"], schedules.c.version == schedule["version"]).values(**values))
                claimed.append((str(schedule["schedule_id"]), occurrence_id))
        completed: list[str] = []
        for schedule_id, occurrence_id in claimed:
            try:
                self._materialize(schedule_id, occurrence_id)
                completed.append(occurrence_id)
            except Exception:
                # The claimed occurrence remains durable and will be recovered
                # after its lease. Do not create a second turn here.
                continue
        return tuple(completed)

    def _next_after_rule(self, rule: dict[str, object], timezone: str, logical_at: datetime) -> datetime | None:
        kind = str(rule.get("kind") or "once")
        if kind == "once":
            return None
        if kind == "hourly":
            return logical_at + timedelta(hours=1)
        zone = self._zone(timezone)
        local = logical_at.astimezone(zone)
        request = ScheduledChatInput("x", "x", "x", timezone, kind, time_of_day=str(rule.get("time_of_day") or ""), weekday=rule.get("weekday") if isinstance(rule.get("weekday"), int) else None)
        return self.next_fire(request, after=local.astimezone(UTC) + timedelta(seconds=1))

    def _materialize(self, schedule_id: str, occurrence_id: str) -> None:
        with self._engine.connect() as connection:
            row = connection.execute(select(scheduled_chat_tasks).where(scheduled_chat_tasks.c.schedule_id == schedule_id)).mappings().one()
            occurrence = connection.execute(select(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == occurrence_id)).mappings().one()
            if occurrence["execution_id"]:
                return
            model_ok = connection.execute(select(provider_model_catalog.c.id).where(
                provider_model_catalog.c.user_id == row["user_id"], provider_model_catalog.c.provider == row["provider"], provider_model_catalog.c.model_id == row["model_id"],
            )).scalar_one_or_none()
            provider_ok = connection.execute(select(provider_configurations.c.user_id).where(
                provider_configurations.c.user_id == row["user_id"], provider_configurations.c.provider == row["provider"], provider_configurations.c.enabled.is_(True),
            )).scalar_one_or_none()
            if model_ok is None or provider_ok is None:
                raise ValueError("scheduled model is no longer authorized")
        # ChatStore's user+idempotency constraint makes this safe across a
        # process crash between the turn write and occurrence update.
        receipt = self._chat.create(
            user_id=str(row["user_id"]), message=str(row["message"]), provider=str(row["provider"]), model_id=str(row["model_id"]),
            idempotency_key=f"schedule:{occurrence_id}", conversation_id=str(row["conversation_id"]) if row["conversation_id"] else None,
            project_id=str(row["project_id"]) if row["project_id"] else None, scheduled_by_schedule_id=schedule_id,
        )
        now = self._clock()
        with self._engine.begin() as connection:
            connection.execute(update(scheduled_chat_tasks).where(scheduled_chat_tasks.c.schedule_id == schedule_id, scheduled_chat_tasks.c.conversation_id.is_(None)).values(conversation_id=receipt.conversation_id, updated_at=now))
            connection.execute(update(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == occurrence_id, schedule_occurrences.c.state == "CLAIMED").values(
                state="DISPATCHED", execution_id=PostgresChatStore.execution_id_for(receipt.turn_id), dispatch_id=f"chat:{receipt.turn_id}",
                version=schedule_occurrences.c.version + 1, claim_id=None, claim_owner=None, claim_expires_at=None, updated_at=now,
            ))


__all__ = ["ScheduledChatInput", "ScheduledChatService"]
