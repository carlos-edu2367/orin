"""ScheduleWakeup: continue this conversation after a delay (W4).

A wakeup is a one-shot row of Orin's scheduler, bound to this conversation; when it fires the scheduler
starts an ordinary turn whose message is the wakeup's prompt. A conversation has at most one pending
wakeup (a new one replaces it, ``stop`` cancels it) and at most ``MAX_WAKEUPS_PER_DAY`` in any 24 hours,
so a loop that keeps rescheduling itself cannot run unattended forever.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolInput, ToolSpec

MIN_DELAY_SECONDS = 60
MAX_DELAY_SECONDS = 3600
MAX_WAKEUPS_PER_DAY = 200
MAX_REASON_CHARS = 200
MAX_WAKEUP_PROMPT_CHARS = 16_000


def _flag(value: object) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


class WakeupTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def schedule_wakeup(
        self, delay_seconds: int | float | str | None = None, reason: str = "", prompt: str = "",
        stop: bool | None = None, noop: bool | None = None,
    ) -> dict[str, Any]:
        context = self._scheduling()
        user_id, conversation_id = context["user_id"], str(self.conversation_id)
        stopping = _flag(stop)
        why = " ".join(str(reason or "").split())[:MAX_REASON_CHARS]
        text = str(prompt or "").strip()
        if not stopping:
            if not why:
                raise AgentToolError("reason must say, in one short sentence, why this delay was chosen")
            if not text or len(text) > MAX_WAKEUP_PROMPT_CHARS:
                raise AgentToolError(f"prompt must be a non-blank string of at most {MAX_WAKEUP_PROMPT_CHARS} characters")
            try:
                requested = float(delay_seconds)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise AgentToolError("delaySeconds must be a number of seconds") from None
            if requested != requested:  # NaN
                raise AgentToolError("delaySeconds must be a number of seconds")
            since = datetime.now(UTC) - timedelta(days=1)
            if self._scheduler.count_created_since(user_id, conversation_id, source="wakeup", since=since) >= MAX_WAKEUPS_PER_DAY:
                raise AgentToolError(f"This conversation already scheduled {MAX_WAKEUPS_PER_DAY} wakeups in the last 24 hours; stop the loop here.")
        cancelled = 0
        for row in self._scheduler.list_for_conversation(user_id, conversation_id, sources=("wakeup",)):
            if self._scheduler.cancel(user_id, str(row["schedule_id"])):
                cancelled += 1
        if stopping:
            return {
                "summary": "Parou os despertares agendados",
                "content": f"Stopped. {cancelled} pending wakeup(s) cancelled; nothing else is scheduled by ScheduleWakeup.",
                "payload": {"stopped": True, "cancelled": cancelled},
                "tool_output": {"scheduledFor": None, "clampedDelaySeconds": 0, "wasClamped": False, "stopped": True, "cancelledWakeups": cancelled},
            }
        clamped = int(round(min(MAX_DELAY_SECONDS, max(MIN_DELAY_SECONDS, requested))))
        was_clamped = not MIN_DELAY_SECONDS <= requested <= MAX_DELAY_SECONDS
        from agentos.scheduler.scheduled_chats import ScheduledChatInput

        fire_at = datetime.now(UTC) + timedelta(seconds=clamped)
        request = ScheduledChatInput(
            text, context["provider"], context["model_id"], str(context.get("timezone") or "UTC"), "once",
            fire_at=fire_at, conversation_id=conversation_id, source="wakeup",
        )
        try:
            receipt = self._scheduler.create(user_id, request, idempotency_key=f"wakeup:{conversation_id}:{uuid4().hex}")
        except (LookupError, ValueError) as error:
            raise AgentToolError(f"The wakeup could not be scheduled: {error}") from None
        scheduled_for = int(fire_at.timestamp() * 1000)
        return {
            "summary": f"Volta em {clamped}s: {why}"[:240],
            "content": (
                f"Wakeup {receipt['schedule_id']} scheduled in {clamped}s ({fire_at.isoformat().replace('+00:00', 'Z')})"
                + (f"; the requested delay was clamped to [{MIN_DELAY_SECONDS}, {MAX_DELAY_SECONDS}] seconds" if was_clamped else "")
                + (f"; {cancelled} earlier wakeup(s) replaced" if cancelled else "") + "."
            ),
            "payload": {"schedule_id": receipt["schedule_id"], "reason": why, "noop": _flag(noop), "label": why[:120]},
            "tool_output": {
                "scheduledFor": scheduled_for, "clampedDelaySeconds": clamped, "wasClamped": was_clamped,
                "stopped": False, "cancelledWakeups": cancelled,
            },
        }


class ScheduleWakeupInput(ToolInput):
    delaySeconds: float | None = Field(None, description="Seconds from now to continue; clamped to [60, 3600].")
    reason: str | None = Field(None, description="One short sentence on why this delay was chosen.")
    prompt: str | None = Field(None, description="What to do when the wakeup fires; it starts a turn in this conversation.")
    stop: bool | None = Field(None, description="Cancel the pending wakeup and stop the loop; the other fields are ignored.")
    noop: bool | None = Field(None, description="True when nothing changed since the last wakeup.")


class ScheduleWakeupOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheduledFor: int | None = None
    clampedDelaySeconds: int
    wasClamped: bool
    stopped: bool | None = None
    cancelledWakeups: int | None = None


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="schedule_wakeup",
        description="Continue this conversation after a delay (60 to 3600 seconds) with the given prompt. A new wakeup replaces the pending one; stop=true cancels it.",
        legacy_schema=_schema({
            "delay_seconds": {"type": "number", "description": "Seconds from now; clamped to [60, 3600]."},
            "reason": _TEXT,
            "prompt": _TEXT,
            "stop": {"type": "boolean"},
            "noop": {"type": "boolean"},
        }),
        handler="schedule_wakeup", kind="planning",
        input_model=ScheduleWakeupInput, output_model=ScheduleWakeupOutput,
        public_description="Continue this conversation after a delay of 60 to 3600 seconds with the given prompt. A new wakeup replaces the pending one; stop cancels it.",
        available=lambda toolset: toolset._scheduler is not None and bool(toolset.conversation_id), order=1040,
    ),
)
