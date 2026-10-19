"""CronCreate, CronDelete and CronList: prompts that run later in this conversation (W4).

Jobs are rows of Orin's own scheduler (the same service the Schedules page uses), bound to this
conversation and to the model of the turn that made them; the scheduler worker starts each run as an
ordinary turn. A recurring job stops after 7 days, as Claude Code's does. Jobs are always durable: they
survive a restart (deviation: Claude Code keeps non-durable jobs in memory only).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolInput, ToolSpec
from agentos.scheduler.cron import CronError, human_schedule, next_after, parse_cron

MAX_CONVERSATION_JOBS = 20
RECURRING_DAYS = 7
MAX_JOB_PROMPT_CHARS = 16_000


def _epoch_ms(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


class CronTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def _scheduling(self) -> dict[str, str]:
        context = dict(self._schedule_context or {})
        if self._scheduler is None or not self.conversation_id or not context.get("user_id"):
            raise AgentToolError("Scheduling is not available in this conversation.")
        return context

    def _job(self, row: dict[str, Any]) -> dict[str, Any]:
        expression = str(row.get("cron") or "")
        timezone = str(row.get("timezone") or "UTC")
        try:
            human = human_schedule(parse_cron(expression), timezone) if expression else "once"
        except CronError:
            human = expression or "once"
        recurring = str(row.get("recurrence") or "") == "cron"
        return {
            "id": str(row["schedule_id"]), "cron": expression, "humanSchedule": human if recurring else f"once ({human})",
            "prompt": str(row.get("message") or ""), "recurring": recurring, "durable": True,
            "nextFireAt": _epoch_ms(row.get("next_fire_at")),
        }

    def cron_create(self, cron: str, prompt: str, recurring: bool | None = True, durable: bool | None = None) -> dict[str, Any]:
        context = self._scheduling()
        # A null or omitted flag keeps the default (recurring); only an explicit false makes a one-shot job.
        recurring = recurring is not False and str(recurring).strip().lower() not in {"false", "0", "no"}
        text = str(prompt or "").strip()
        if not text or len(text) > MAX_JOB_PROMPT_CHARS:
            raise AgentToolError(f"prompt must be a non-blank string of at most {MAX_JOB_PROMPT_CHARS} characters")
        try:
            spec = parse_cron(cron)
        except CronError as error:
            raise AgentToolError(f"Invalid cron expression: {error}") from None
        timezone = str(context.get("timezone") or "UTC")
        existing = self._scheduler.list_for_conversation(context["user_id"], self.conversation_id, sources=("cron",))
        if len(existing) >= MAX_CONVERSATION_JOBS:
            raise AgentToolError(f"This conversation already has {MAX_CONVERSATION_JOBS} scheduled jobs; delete one with CronDelete first.")
        from agentos.scheduler.scheduled_chats import ScheduledChatInput

        now = datetime.now(UTC)
        common = dict(conversation_id=self.conversation_id, source="cron", cron=spec.expression)
        if recurring:
            request = ScheduledChatInput(
                text, context["provider"], context["model_id"], timezone, "cron",
                ends_at=now + timedelta(days=RECURRING_DAYS), **common,
            )
        else:
            try:
                first = next_after(spec, now.astimezone(ZoneInfo(timezone)))
            except CronError as error:
                raise AgentToolError(f"Invalid cron expression: {error}") from None
            request = ScheduledChatInput(text, context["provider"], context["model_id"], timezone, "once", fire_at=first, **common)
        try:
            receipt = self._scheduler.create(context["user_id"], request, idempotency_key=f"cron:{self.conversation_id}:{uuid4().hex}")
        except (LookupError, ValueError) as error:
            raise AgentToolError(f"The job could not be scheduled: {error}") from None
        human = human_schedule(spec, timezone)
        if not recurring:
            human = f"once ({human})"
        next_at = str(receipt.get("next_fire_at") or "")
        notes = f" It expires after {RECURRING_DAYS} days." if recurring else ""
        return {
            "summary": f"Agendou: {human}"[:240],
            "content": f"Scheduled job {receipt['schedule_id']}: {human}; next run {next_at}.{notes} Jobs survive a restart of Orin.",
            "payload": {"schedule_id": receipt["schedule_id"], "label": human[:120]},
            "tool_output": {
                "id": str(receipt["schedule_id"]), "humanSchedule": human, "recurring": bool(recurring),
                "durable": True, "nextFireAt": _epoch_ms(next_at),
            },
        }

    def cron_delete(self, id: str) -> dict[str, Any]:  # noqa: A002 - the public field is called id
        context = self._scheduling()
        job_id = str(id or "").strip()
        owned = {str(row["schedule_id"]) for row in self._scheduler.list_for_conversation(context["user_id"], self.conversation_id, sources=("cron",))}
        if job_id not in owned:
            raise AgentToolError(f"No scheduled job '{job_id[:80]}' in this conversation.")
        self._scheduler.cancel(context["user_id"], job_id)
        return {"summary": "Cancelou um agendamento", "content": f"Deleted job {job_id}.", "payload": {"schedule_id": job_id}, "tool_output": {"id": job_id}}

    def cron_list(self) -> dict[str, Any]:
        context = self._scheduling()
        jobs = [self._job(row) for row in self._scheduler.list_for_conversation(context["user_id"], self.conversation_id, sources=("cron",))]
        body = "\n".join(f"{job['id']}  {job['humanSchedule']}  next: {job['nextFireAt']}  — {job['prompt'][:120]}" for job in jobs) or "[no scheduled jobs]"
        return {"summary": f"{len(jobs)} agendamento(s)", "content": body, "payload": {"count": len(jobs)}, "tool_output": {"jobs": jobs}}


class CronCreateInput(ToolInput):
    cron: str = Field(description="Five-field cron expression (minute hour day-of-month month day-of-week), in local time.")
    prompt: str = Field(description="What to do at each run; it starts a turn in this conversation.")
    recurring: bool | None = Field(None, description="Run at every match (default, stops after 7 days) or only once.")
    durable: bool | None = Field(None, description="Orin always keeps jobs across restarts.")


class CronDeleteInput(ToolInput):
    id: str = Field(description="Id returned by CronCreate.")


class CronListInput(ToolInput):
    pass


class CronCreateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    humanSchedule: str
    recurring: bool
    durable: bool
    nextFireAt: int | None = None


class CronDeleteOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str


class CronJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    cron: str
    humanSchedule: str
    prompt: str
    recurring: bool
    durable: bool
    nextFireAt: int | None = None


class CronListOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jobs: list[CronJob]


def _available(toolset: Any) -> bool:
    return toolset._scheduler is not None and bool(toolset.conversation_id)


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="cron_create",
        description="Schedule a prompt to run later in this conversation on a five-field cron expression (local time). Recurring jobs stop after 7 days.",
        legacy_schema=_schema({
            "cron": {**_TEXT, "description": "Five-field cron expression, e.g. '0 9 * * 1-5'."},
            "prompt": _TEXT,
            "recurring": {"type": "boolean"},
            "durable": {"type": "boolean"},
        }, ("cron", "prompt")),
        handler="cron_create", kind="planning", policy_tags=("mutates",),
        input_model=CronCreateInput, output_model=CronCreateOutput,
        public_description="Schedule a prompt to run later in this conversation on a cron expression in local time. Recurring jobs stop after 7 days.",
        available=_available, order=1010,
    ),
    ToolSpec(
        key="cron_delete",
        description="Delete a job this conversation scheduled with cron_create.",
        legacy_schema=_schema({"id": _TEXT}, ("id",)),
        handler="cron_delete", kind="planning", policy_tags=("mutates",),
        input_model=CronDeleteInput, output_model=CronDeleteOutput,
        public_description="Delete a job this conversation scheduled with CronCreate.",
        available=_available, order=1020,
    ),
    ToolSpec(
        key="cron_list",
        description="List the jobs this conversation scheduled with cron_create.",
        legacy_schema=_schema({}),
        handler="cron_list", kind="planning", read_only=True,
        input_model=CronListInput, output_model=CronListOutput,
        public_description="List the jobs this conversation scheduled with CronCreate.",
        available=_available, order=1030,
    ),
)
