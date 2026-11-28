"""Durable scheduled-chat application service.

The scheduler owns time and occurrence state; the existing chat publisher and
worker remain the only code that executes a model turn.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.conversations.chat import PostgresChatStore
from agentos.scheduler.cron import next_after, parse_cron
from agentos.persistence.postgres.schema import (
    conversation_turns, conversations, provider_configurations, provider_model_catalog,
    projects, schedule_occurrences, scheduled_chat_tasks, schedules, users,
)


RULES = frozenset({"once", "hourly", "daily", "weekly", "cron"})
# W4-R7: a run bound to a conversation with one of these turns waits. A queued turn holds no pending prompt and
# the run queues behind it as before, so "queued" does not defer.
BUSY_TURN_STATES = ("waiting_user", "running")
# W7 (W4): the schedules a tool made (wakeups, monitor wakes) are deleted a week after they finish; the person's are kept.
TOOL_SCHEDULE_SOURCES = frozenset({"wakeup", "monitor", "subagent"})
FINAL_SCHEDULE_STATES = ("EXPIRED", "CANCELLED")
TOOL_SCHEDULE_RETENTION = timedelta(days=7)
TOOL_SCHEDULE_PRUNE_EVERY = timedelta(hours=1)
logger = logging.getLogger(__name__)


class _DeferredRun(Exception):
    """The conversation a run is bound to is busy; the claimed occurrence is retried at a later poll."""


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
    # W4: a five-field expression for recurrence="cron"; the conversation the runs belong to (a tool-made
    # schedule); when it stops (recurring CronCreate jobs expire after 7 days); and who made it.
    cron: str | None = None
    conversation_id: str | None = None
    ends_at: datetime | None = None
    source: str = "user"
    wake_on_pid: int | None = None


@dataclass(frozen=True, slots=True)
class ScheduledChatUpdate:
    message: str | None = None
    provider: str | None = None
    model_id: str | None = None
    timezone: str | None = None
    recurrence: str | None = None
    fire_at: datetime | None = None
    time_of_day: str | None = None
    weekday: int | None = None
    cron: str | None = None
    project_id: str | None = None
    project_id_set: bool = False
    state: str | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("fire_at must include a timezone")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    """SQLite returns naive timestamps although the production column is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None or value.utcoffset() is None else value.astimezone(UTC)


def _iso_utc(value: datetime | None) -> str | None:
    """Serialize stored UTC timestamps with an explicit offset for clients."""
    if value is None:
        return None
    return _stored_utc(value).isoformat().replace("+00:00", "Z")


def _clock_now() -> datetime:
    return datetime.now(UTC)


class ScheduledChatService:
    """Creates and materializes user-owned scheduled chat turns."""

    def __init__(self, engine: Engine, *, clock=_clock_now) -> None:
        self._engine = engine
        self._chat = PostgresChatStore(engine)
        self._clock = clock
        self._pruned_at: datetime | None = None

    def prune_finished(self, *, now: datetime | None = None) -> int:
        """Delete the finished schedules a tool made (wakeups, monitor wakes) once they are a week old (W7, W4).

        Only ``wakeup`` and ``monitor`` schedules in a final state, unchanged for ``TOOL_SCHEDULE_RETENTION``, with no
        run still in flight; the person's own schedules are never deleted here. Returns how many were deleted.
        """
        cutoff = _utc(now or self._clock()) - TOOL_SCHEDULE_RETENTION
        with self._engine.begin() as connection:
            rows = connection.execute(select(schedules.c.schedule_id, schedules.c.rule, schedules.c.updated_at).where(
                schedules.c.state.in_(FINAL_SCHEDULE_STATES),
            )).all()
            old = [str(row.schedule_id) for row in rows
                   if str(dict(row.rule or {}).get("source") or "user") in TOOL_SCHEDULE_SOURCES and _stored_utc(row.updated_at) < cutoff]
            if not old:
                return 0
            busy = {str(item) for item in connection.execute(select(schedule_occurrences.c.schedule_id).where(
                schedule_occurrences.c.schedule_id.in_(old), schedule_occurrences.c.state.in_(("CLAIMED", "MATERIALIZED")),
            )).scalars()}
            prune = [schedule_id for schedule_id in old if schedule_id not in busy]
            if prune:
                # Everything that points at the schedule goes with it; a past turn keeps its schedule id (a plain column).
                connection.execute(delete(schedule_occurrences).where(schedule_occurrences.c.schedule_id.in_(prune)))
                connection.execute(delete(scheduled_chat_tasks).where(scheduled_chat_tasks.c.schedule_id.in_(prune)))
                connection.execute(delete(schedules).where(schedules.c.schedule_id.in_(prune)))
        return len(prune)

    def _maybe_prune(self) -> None:
        now = _utc(self._clock())
        if self._pruned_at is not None and now - self._pruned_at < TOOL_SCHEDULE_PRUNE_EVERY:
            return
        self._pruned_at = now
        try:
            self.prune_finished(now=now)
        except Exception:  # noqa: BLE001 - housekeeping never stops a run
            logger.exception("finished tool schedules could not be pruned")

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
            # A datetime-local value has no offset. In that case it is a wall
            # clock value in the schedule's selected IANA timezone, not in the
            # server or browser timezone.
            fire_at = request.fire_at
            if fire_at.tzinfo is None or fire_at.utcoffset() is None:
                fire_at = fire_at.replace(tzinfo=zone).astimezone(UTC)
            else:
                fire_at = _utc(fire_at)
            if fire_at <= now:
                raise ValueError("fire_at must be in the future")
            return fire_at
        if request.recurrence == "cron":
            spec = parse_cron(str(request.cron or ""))
            return next_after(spec, now.astimezone(zone)).astimezone(UTC)
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

    @staticmethod
    def _validate_model(connection, user_id: str, provider_name: str, model_id: str) -> None:
        # Do not allow a stale or forged UI model selection to persist.
        model = connection.execute(select(provider_model_catalog.c.id).join(
            provider_configurations,
            (provider_configurations.c.user_id == provider_model_catalog.c.user_id)
            & (provider_configurations.c.provider == provider_model_catalog.c.provider),
        ).where(
            provider_model_catalog.c.user_id == user_id,
            provider_model_catalog.c.provider == provider_name,
            provider_model_catalog.c.model_id == model_id,
            provider_configurations.c.enabled.is_(True),
            (
                provider_model_catalog.c.is_custom.is_(True)
                | provider_configurations.c.catalog_refreshed_at.is_not(None)
            ),
            (
                (provider_model_catalog.c.catalog_base_url == provider_configurations.c.base_url)
                | (
                    provider_model_catalog.c.catalog_base_url.is_(None)
                    & provider_configurations.c.base_url.is_(None)
                )
            ),
        )).scalar_one_or_none()
        if model is None:
            raise ValueError("model is not authorized")
        provider = connection.execute(select(provider_configurations.c.user_id).where(
            provider_configurations.c.user_id == user_id,
            provider_configurations.c.provider == provider_name,
            provider_configurations.c.enabled.is_(True),
        )).scalar_one_or_none()
        if provider is None:
            raise ValueError("provider is not enabled")

    def create(self, user_id: str, request: ScheduledChatInput, *, idempotency_key: str) -> dict[str, object]:
        message = (
            "\n".join(" ".join(line.split()) for line in request.message.splitlines() if line.strip())
            if request.source == "subagent"
            else " ".join(request.message.split())
        )
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
            if request.conversation_id:
                owner = connection.execute(select(conversations.c.user_id).where(
                    conversations.c.conversation_id == request.conversation_id,
                )).scalar_one_or_none()
                if owner != user_id:
                    raise LookupError("conversation not found")
            self._validate_model(connection, user_id, request.provider, request.model_id)
            # The task points back to its schedule through a foreign key. Create
            # the schedule first, then its task, while keeping both writes in
            # this transaction so a partial schedule can never be committed.
            connection.execute(insert(schedules).values(
                schedule_id=schedule_id, user_id=user_id, workspace_id=workspace_id,
                agent_id=f"agent:schedule:{schedule_id}", schedule_type="FUTURE_EXECUTION",
                target={"kind": "TASK", "immutable_ref": task_id, "destination_pool": "AGENT"},
                rule=rule, timezone=request.timezone,
                policies={"misfire": {"kind": "FIRE_ONCE_NOW", "grace_seconds": 0, "max_occurrences": 1}, "overlap": {"kind": "SERIALIZE", "max_active": 1}},
                state="ACTIVE", version=1, next_fire_at=next_fire, starts_at=now,
                ends_at=_utc(request.ends_at) if request.ends_at is not None else None,
                idempotency_key=idempotency_key, created_at=now, updated_at=now,
            ))
            connection.execute(insert(scheduled_chat_tasks).values(
                task_id=task_id, schedule_id=schedule_id, user_id=user_id, message=message,
                provider=request.provider, model_id=request.model_id, project_id=request.project_id,
                conversation_id=request.conversation_id, created_at=now, updated_at=now,
            ))
        return {"schedule_id": schedule_id, "state": "ACTIVE", "next_fire_at": _iso_utc(next_fire), "recurrence": request.recurrence}

    @staticmethod
    def _rule(request: ScheduledChatInput, next_fire: datetime) -> dict[str, object]:
        return {
            "kind": request.recurrence, "fire_at": next_fire.isoformat() if request.fire_at else None,
            "time_of_day": request.time_of_day, "weekday": request.weekday,
            "anchor_at": next_fire.isoformat(), "cron": request.cron, "source": request.source,
            **({"wake_on_pid": int(request.wake_on_pid)} if request.wake_on_pid is not None else {}),
        }

    @staticmethod
    def _public(row) -> dict[str, object]:
        rule = dict(row["rule"] or {})
        return {"schedule_id": str(row["schedule_id"]), "state": str(row["state"]), "next_fire_at": _iso_utc(row["next_fire_at"]), "recurrence": str(rule.get("kind") or "once"), "project_id": None}

    def _enrich_rows(self, connection, user_id: str, rows) -> list[dict[str, object]]:
        if not rows:
            return []
        model_rows = connection.execute(
            select(provider_model_catalog.c.provider, provider_model_catalog.c.model_id, provider_model_catalog.c.display_name)
            .where(provider_model_catalog.c.user_id == user_id)
        ).all()
        model_names = {(str(r.provider), str(r.model_id)): str(r.display_name) for r in model_rows if r.display_name}

        conv_ids = list({str(r["conversation_id"]) for r in rows if r["conversation_id"]})
        conv_meta: dict[str, tuple[str | None, str | None]] = {}
        if conv_ids:
            conv_rows = connection.execute(
                select(conversations.c.conversation_id, conversations.c.title, conversations.c.project_id)
                .where(conversations.c.user_id == user_id, conversations.c.conversation_id.in_(conv_ids))
            ).all()
            conv_meta = {
                str(r.conversation_id): (str(r.title) if r.title else None, str(r.project_id) if r.project_id else None)
                for r in conv_rows
            }

        items: list[dict[str, object]] = []
        for row in rows:
            rule = dict(row["rule"] or {})
            source = str(rule.get("source") or "user")
            # A wakeup or monitor wake that already ran is an implementation detail; pending ones stay cancellable here.
            if source in TOOL_SCHEDULE_SOURCES and row["state"] != "ACTIVE":
                continue
            conv_id = str(row["conversation_id"]) if row["conversation_id"] else None
            conv_title, conv_project_id = conv_meta.get(conv_id, (None, None)) if conv_id else (None, None)
            provider = str(row["provider"])
            model_id = str(row["model_id"])
            items.append({
                **self._public(row),
                "project_id": row["project_id"],
                "conversation_project_id": conv_project_id,
                "conversation_title": conv_title,
                "message": row["message"],
                "provider": provider,
                "model_id": model_id,
                "model_display_name": model_names.get((provider, model_id)),
                "conversation_id": conv_id,
                "source": source,
                "timezone": str(row["timezone"]),
                "cron": rule.get("cron") if rule.get("kind") == "cron" else None,
                "time_of_day": rule.get("time_of_day"),
                "weekday": rule.get("weekday"),
                "fire_at": rule.get("fire_at"),
                "ends_at": _iso_utc(row["ends_at"]),
                "created_at": _iso_utc(row["created_at"]),
                "updated_at": _iso_utc(row["updated_at"]),
            })
        return items

    def list(self, user_id: str) -> dict[str, object]:
        with self._engine.connect() as connection:
            rows = connection.execute(select(schedules, scheduled_chat_tasks.c.project_id, scheduled_chat_tasks.c.message, scheduled_chat_tasks.c.provider, scheduled_chat_tasks.c.model_id, scheduled_chat_tasks.c.conversation_id)
                .join(scheduled_chat_tasks, scheduled_chat_tasks.c.schedule_id == schedules.c.schedule_id)
                .where(schedules.c.user_id == user_id).order_by(schedules.c.created_at.desc())).mappings().all()
            items = self._enrich_rows(connection, user_id, rows)
        return {"items": items}

    def update(self, user_id: str, schedule_id: str, patch: ScheduledChatUpdate) -> dict[str, object]:
        """Update message, model/provider, recurrence, timezone, project, or ACTIVE/PAUSED state."""
        now = _utc(self._clock())
        with self._engine.begin() as connection:
            row = connection.execute(
                select(
                    schedules,
                    scheduled_chat_tasks.c.task_id,
                    scheduled_chat_tasks.c.project_id,
                    scheduled_chat_tasks.c.message,
                    scheduled_chat_tasks.c.provider,
                    scheduled_chat_tasks.c.model_id,
                    scheduled_chat_tasks.c.conversation_id,
                )
                .join(scheduled_chat_tasks, scheduled_chat_tasks.c.schedule_id == schedules.c.schedule_id)
                .where(schedules.c.schedule_id == schedule_id, schedules.c.user_id == user_id)
            ).mappings().first()
            if row is None:
                raise LookupError("schedule not found")

            current_rule = dict(row["rule"] or {})
            source = str(current_rule.get("source") or "user")

            if patch.message is not None:
                new_message = "\n".join(line.strip() for line in patch.message.splitlines()).strip()
                if not new_message or len(new_message) > 16000:
                    raise ValueError("message must be a bounded non-blank string")
            else:
                new_message = str(row["message"])

            if patch.provider is not None or patch.model_id is not None:
                new_provider = (patch.provider or "").strip()
                new_model_id = (patch.model_id or "").strip()
                if not new_provider or not new_model_id:
                    raise ValueError("provider and model_id are required")
                self._validate_model(connection, user_id, new_provider, new_model_id)
            else:
                new_provider = str(row["provider"])
                new_model_id = str(row["model_id"])

            if patch.project_id_set:
                if patch.project_id:
                    project = connection.execute(select(projects.c.workspace_id).where(
                        projects.c.project_id == patch.project_id,
                        projects.c.user_id == user_id,
                        projects.c.archived_at.is_(None),
                    )).scalar_one_or_none()
                    if project is None:
                        raise LookupError("project not found")
                    new_project_id: str | None = patch.project_id
                    new_workspace_id: str | None = str(project)
                else:
                    new_project_id = None
                    new_workspace_id = None
            else:
                new_project_id = row["project_id"]
                new_workspace_id = row["workspace_id"]

            new_timezone = patch.timezone if patch.timezone is not None else str(row["timezone"])
            self._zone(new_timezone)

            if patch.state is not None:
                if patch.state not in ("ACTIVE", "PAUSED"):
                    raise ValueError("state must be ACTIVE or PAUSED")
                new_state = patch.state
            else:
                new_state = str(row["state"])

            recurrence_changed = patch.recurrence is not None
            timezone_changed = patch.timezone is not None and patch.timezone != str(row["timezone"])
            becoming_active = new_state == "ACTIVE" and str(row["state"]) != "ACTIVE"

            if recurrence_changed:
                new_kind = str(patch.recurrence)
                req = ScheduledChatInput(
                    message=new_message,
                    provider=new_provider,
                    model_id=new_model_id,
                    timezone=new_timezone,
                    recurrence=new_kind,
                    fire_at=patch.fire_at,
                    time_of_day=patch.time_of_day,
                    weekday=patch.weekday,
                    cron=patch.cron,
                    project_id=new_project_id,
                    source=source,
                    wake_on_pid=current_rule.get("wake_on_pid") if isinstance(current_rule.get("wake_on_pid"), int) else None,
                )
                if new_state == "ACTIVE":
                    next_fire: datetime | None = self.next_fire(req)
                    new_rule = self._rule(req, next_fire)
                else:
                    # Validate the rule even while paused so resuming later is always valid.
                    if new_kind == "once" and req.fire_at is None:
                        raise ValueError("once schedules require fire_at")
                    if new_kind == "cron":
                        parse_cron(str(req.cron or ""))
                    elif new_kind in ("daily", "weekly"):
                        self._time(req.time_of_day)
                        if new_kind == "weekly" and (req.weekday is None or not 0 <= req.weekday <= 6):
                            raise ValueError("weekly schedules require weekday from 0 to 6")
                    elif new_kind not in RULES:
                        raise ValueError("unsupported recurrence")
                    anchor = _utc(req.fire_at) if (req.fire_at is not None and req.fire_at.tzinfo is not None) else (
                        req.fire_at.replace(tzinfo=self._zone(new_timezone)).astimezone(UTC) if req.fire_at is not None else now
                    )
                    next_fire = None
                    new_rule = self._rule(req, anchor)
            elif new_state == "PAUSED":
                next_fire = None
                new_rule = current_rule
            elif timezone_changed or becoming_active:
                cur_kind = str(current_rule.get("kind") or "once")
                raw_fire_at = current_rule.get("fire_at")
                parsed_fire_at = datetime.fromisoformat(raw_fire_at) if isinstance(raw_fire_at, str) and raw_fire_at else None
                req = ScheduledChatInput(
                    message=new_message,
                    provider=new_provider,
                    model_id=new_model_id,
                    timezone=new_timezone,
                    recurrence=cur_kind,
                    fire_at=parsed_fire_at,
                    time_of_day=str(current_rule.get("time_of_day")) if current_rule.get("time_of_day") else None,
                    weekday=current_rule.get("weekday") if isinstance(current_rule.get("weekday"), int) else None,
                    cron=str(current_rule.get("cron")) if current_rule.get("cron") else None,
                    project_id=new_project_id,
                    source=source,
                    wake_on_pid=current_rule.get("wake_on_pid") if isinstance(current_rule.get("wake_on_pid"), int) else None,
                )
                if new_state == "ACTIVE":
                    next_fire = self.next_fire(req)
                    new_rule = self._rule(req, next_fire)
                else:
                    next_fire = row["next_fire_at"]
                    new_rule = current_rule
            else:
                next_fire = row["next_fire_at"]
                new_rule = current_rule

            connection.execute(
                update(schedules)
                .where(schedules.c.schedule_id == schedule_id, schedules.c.user_id == user_id)
                .values(
                    workspace_id=new_workspace_id,
                    rule=new_rule,
                    timezone=new_timezone,
                    state=new_state,
                    next_fire_at=next_fire,
                    version=schedules.c.version + 1,
                    updated_at=now,
                )
            )
            connection.execute(
                update(scheduled_chat_tasks)
                .where(scheduled_chat_tasks.c.schedule_id == schedule_id, scheduled_chat_tasks.c.user_id == user_id)
                .values(
                    message=new_message,
                    provider=new_provider,
                    model_id=new_model_id,
                    project_id=new_project_id,
                    updated_at=now,
                )
            )
            updated_row = connection.execute(
                select(
                    schedules,
                    scheduled_chat_tasks.c.project_id,
                    scheduled_chat_tasks.c.message,
                    scheduled_chat_tasks.c.provider,
                    scheduled_chat_tasks.c.model_id,
                    scheduled_chat_tasks.c.conversation_id,
                )
                .join(scheduled_chat_tasks, scheduled_chat_tasks.c.schedule_id == schedules.c.schedule_id)
                .where(schedules.c.schedule_id == schedule_id, schedules.c.user_id == user_id)
            ).mappings().one()
            enriched = self._enrich_rows(connection, user_id, [updated_row])
        return enriched[0] if enriched else self._public(updated_row)

    def list_for_conversation(self, user_id: str, conversation_id: str, *, sources: tuple[str, ...]) -> list[dict[str, object]]:
        """Active schedules a tool made for one of this person's conversations, oldest first."""
        with self._engine.connect() as connection:
            rows = connection.execute(select(schedules, scheduled_chat_tasks.c.message).join(
                scheduled_chat_tasks, scheduled_chat_tasks.c.schedule_id == schedules.c.schedule_id,
            ).where(
                schedules.c.user_id == user_id, scheduled_chat_tasks.c.conversation_id == conversation_id,
                schedules.c.state == "ACTIVE",
            ).order_by(schedules.c.created_at)).mappings().all()
        out: list[dict[str, object]] = []
        for row in rows:
            rule = dict(row["rule"] or {})
            if str(rule.get("source") or "user") not in sources:
                continue
            out.append({
                **self._public(row), "message": row["message"], "cron": rule.get("cron"),
                "source": rule.get("source"), "ends_at": _iso_utc(row["ends_at"]), "timezone": str(row["timezone"]),
                "wake_on_pid": rule.get("wake_on_pid"),
            })
        return out

    def trigger_wakeup_for_pid(
        self,
        user_id: str,
        conversation_id: str,
        pid: int,
        *,
        exit_notice: str = "",
        worker_id: str = "process-watch",
    ) -> str | None:
        """Fire an active wakeup bound to ``pid`` immediately through ``run_due`` with ``exit_notice`` appended."""
        now = _utc(self._clock())
        target_schedule_id: str | None = None
        with self._engine.begin() as connection:
            rows = connection.execute(
                select(schedules, scheduled_chat_tasks.c.task_id, scheduled_chat_tasks.c.message)
                .join(scheduled_chat_tasks, scheduled_chat_tasks.c.schedule_id == schedules.c.schedule_id)
                .where(
                    schedules.c.user_id == user_id,
                    scheduled_chat_tasks.c.conversation_id == conversation_id,
                    schedules.c.state == "ACTIVE",
                )
                .order_by(schedules.c.created_at)
            ).mappings().all()
            for row in rows:
                rule = dict(row["rule"] or {})
                if str(rule.get("source") or "user") != "wakeup":
                    continue
                if rule.get("wake_on_pid") != pid:
                    continue
                target_schedule_id = str(row["schedule_id"])
                base_msg = str(row["message"] or "").strip()
                clean_notice = str(exit_notice or "").strip()
                new_msg = f"{base_msg}\n\n{clean_notice}"[:16000] if clean_notice else base_msg
                connection.execute(
                    update(scheduled_chat_tasks)
                    .where(scheduled_chat_tasks.c.task_id == row["task_id"])
                    .values(message=new_msg, updated_at=now)
                )
                connection.execute(
                    update(schedules)
                    .where(schedules.c.schedule_id == target_schedule_id, schedules.c.state == "ACTIVE")
                    .values(next_fire_at=now, updated_at=now)
                )
                break
        if target_schedule_id is None:
            return None
        self.run_due(worker_id=worker_id, due_before=now)
        return target_schedule_id

    def count_created_since(self, user_id: str, conversation_id: str, *, source: str, since: datetime) -> int:
        """How many schedules a tool made for this conversation since ``since``, in any state (W4 wakeup cap)."""
        with self._engine.connect() as connection:
            rules = connection.execute(select(schedules.c.rule).join(
                scheduled_chat_tasks, scheduled_chat_tasks.c.schedule_id == schedules.c.schedule_id,
            ).where(
                schedules.c.user_id == user_id, scheduled_chat_tasks.c.conversation_id == conversation_id,
                schedules.c.created_at >= _utc(since),
            )).scalars().all()
        return sum(1 for rule in rules if str(dict(rule or {}).get("source") or "user") == source)

    def cancel(self, user_id: str, schedule_id: str) -> bool:
        now = self._clock()
        with self._engine.begin() as connection:
            result = connection.execute(update(schedules).where(
                schedules.c.schedule_id == schedule_id, schedules.c.user_id == user_id,
                schedules.c.state.in_(("ACTIVE", "PAUSED")),
            ).values(state="CANCELLED", version=schedules.c.version + 1, next_fire_at=None, updated_at=now))
        return bool(result.rowcount)

    def delete(self, user_id: str, schedule_id: str) -> bool:
        """Permanently delete a user's schedule and its occurrences/task, from any state."""
        with self._engine.begin() as connection:
            exists = connection.execute(
                select(schedules.c.schedule_id)
                .where(schedules.c.schedule_id == schedule_id, schedules.c.user_id == user_id)
            ).scalar_one_or_none()
            if exists is None:
                return False
            busy = connection.execute(
                select(schedule_occurrences.c.occurrence_id)
                .where(
                    schedule_occurrences.c.schedule_id == schedule_id,
                    schedule_occurrences.c.state.in_(("CLAIMED", "MATERIALIZED")),
                )
                .limit(1)
            ).first()
            if busy is not None:
                raise RuntimeError("schedule has a run in flight")
            connection.execute(delete(schedule_occurrences).where(schedule_occurrences.c.schedule_id == schedule_id))
            connection.execute(delete(scheduled_chat_tasks).where(scheduled_chat_tasks.c.schedule_id == schedule_id))
            connection.execute(delete(schedules).where(schedules.c.schedule_id == schedule_id, schedules.c.user_id == user_id))
        return True

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
            # A deactivated profile's schedules pause; an owner without an
            # account row (the local install) keeps firing as before.
            inactive_owners = select(users.c.user_id).where(users.c.active.is_(False))
            rows = connection.execute(select(schedules).where(
                schedules.c.state == "ACTIVE", schedules.c.next_fire_at <= due,
                schedules.c.user_id.not_in(inactive_owners),
            ).with_for_update()).mappings().all()
            for schedule in rows:
                active = connection.execute(select(schedule_occurrences.c.occurrence_id).where(
                    schedule_occurrences.c.schedule_id == schedule["schedule_id"],
                    schedule_occurrences.c.state.in_(("CLAIMED", "MATERIALIZED")),
                )).scalar_one_or_none()
                if active is not None:
                    continue
                # W4-R7: a busy conversation defers the run. Nothing is claimed and next_fire_at stays, so a later poll retries it.
                if self._conversation_busy(connection, str(schedule["schedule_id"])):
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
                next_at = self._next_after_rule(rule, str(schedule["timezone"]), logical_at, now)
                ends_at = schedule.get("ends_at")
                if next_at is not None and ends_at is not None and next_at > _stored_utc(ends_at):
                    next_at = None  # past its end: this was the last run
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
            except _DeferredRun:
                # W4-R7: the claim stays durable and is retried at a later poll; no turn is created.
                continue
            except Exception:
                # The claimed occurrence remains durable and will be recovered
                # after its lease. Do not create a second turn here.
                continue
        self._maybe_prune()
        return tuple(completed)

    def _next_after_rule(self, rule: dict[str, object], timezone: str, logical_at: datetime, now: datetime) -> datetime | None:
        """The first run strictly after the later of the logical time and now (W4-R8): missed runs coalesce into one."""
        kind = str(rule.get("kind") or "once")
        if kind == "once":
            return None
        base = max(logical_at, now)
        if kind == "hourly":
            # The grid stays anchored to the logical time, so a late poll does not drift the schedule.
            return logical_at + (((base - logical_at) // timedelta(hours=1)) + 1) * timedelta(hours=1)
        zone = self._zone(timezone)
        if kind == "cron":
            return next_after(parse_cron(str(rule.get("cron") or "")), base.astimezone(zone)).astimezone(UTC)
        request = ScheduledChatInput("x", "x", "x", timezone, kind, time_of_day=str(rule.get("time_of_day") or ""), weekday=rule.get("weekday") if isinstance(rule.get("weekday"), int) else None)
        return self.next_fire(request, after=base + timedelta(seconds=1))

    @staticmethod
    def _conversation_busy(connection, schedule_id: str) -> bool:
        """W4-R7: whether the conversation a schedule is bound to has a turn that waits on the person, queued or running."""
        conversation_id = connection.execute(select(scheduled_chat_tasks.c.conversation_id).where(
            scheduled_chat_tasks.c.schedule_id == schedule_id,
        )).scalar_one_or_none()
        if conversation_id is None:
            return False
        return connection.execute(select(conversation_turns.c.turn_id).where(
            conversation_turns.c.conversation_id == conversation_id,
            conversation_turns.c.state.in_(BUSY_TURN_STATES),
        ).limit(1)).first() is not None

    def _materialize(self, schedule_id: str, occurrence_id: str) -> None:
        with self._engine.connect() as connection:
            row = connection.execute(select(scheduled_chat_tasks).where(scheduled_chat_tasks.c.schedule_id == schedule_id)).mappings().one()
            occurrence = connection.execute(select(schedule_occurrences).where(schedule_occurrences.c.occurrence_id == occurrence_id)).mappings().one()
            if occurrence["execution_id"]:
                return
            if self._conversation_busy(connection, schedule_id):
                raise _DeferredRun(occurrence_id)
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


__all__ = ["ScheduledChatInput", "ScheduledChatService", "ScheduledChatUpdate"]
