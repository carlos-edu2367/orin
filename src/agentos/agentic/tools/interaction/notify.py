"""PushNotification: tell the person something without waiting for an answer (W4).

The message becomes a ``notification.pushed`` activity event: the open Orin window shows it as a toast,
or as a system notification when the window is hidden, and it stays in the conversation's timeline. A
Notification hook is fired with it. There is no phone push in Orin, so ``pushSent`` is always false.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.lifecycle import HookEvent
from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolInput, ToolSpec

logger = logging.getLogger("agentos.agentic.agent_tools")

# UTF-16 code units, the unit the web client and the desktop notifier count in: 180 is the smallest limit a renderer applies (W4-R14).
MAX_NOTIFICATION_UNITS = 180
MAX_NOTIFICATIONS_PER_TURN = 3


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class NotificationBudget:
    """The notifications one turn may still show: one counter for the main agent and every subagent (W4-R14)."""

    def __init__(self, limit: int = MAX_NOTIFICATIONS_PER_TURN) -> None:
        self._limit = limit
        self._used = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self._used >= self._limit:
                return False
            self._used += 1
            return True


class NotifyTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def push_notification(self, message: str, status: str | None = None) -> dict[str, Any]:
        text = " ".join(str(message or "").split())
        if not text:
            raise AgentToolError("message must be a non-blank string")
        if _utf16_units(text) > MAX_NOTIFICATION_UNITS:
            kept: list[str] = []
            units = 0
            for character in text:
                width = _utf16_units(character)
                if units + width > MAX_NOTIFICATION_UNITS - 1:  # the ellipsis takes one unit
                    break
                kept.append(character)
                units += width
            text = "".join(kept).rstrip() + "…"
        if not self._notification_budget.take():
            raise AgentToolError(f"At most {MAX_NOTIFICATIONS_PER_TURN} notifications per turn; put the rest in your answer.")
        sent_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        local_sent = False
        if self._on_notification is not None:
            try:
                self._on_notification(text)
                local_sent = True
            except Exception:  # noqa: BLE001 - a failed notification is reported, never raised
                logger.warning("push notification could not be recorded", exc_info=True)
        if self._lifecycle is not None:
            self._emit_lifecycle(self._lifecycle, HookEvent.NOTIFICATION, message=text, title="Orin", notification_type="push_notification")
        output: dict[str, Any] = {"message": text, "localSent": local_sent, "pushSent": False, "sentAt": sent_at}
        if not local_sent:
            output["disabledReason"] = "no_transport"
        return {
            "summary": f"Notificou: {text}"[:240],
            "content": "Notification shown to the person." if local_sent else "The notification could not be shown; say it in your answer instead.",
            "payload": {"message": text, "local_sent": local_sent, "label": text[:120]},
            "tool_output": output,
        }


class PushNotificationInput(ToolInput):
    message: str = Field(description="What to tell the person, in one short sentence (at most 180 characters).")
    status: str | None = Field(None, description="Always \"proactive\".")


class PushNotificationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str
    localSent: bool
    pushSent: bool
    sentAt: str | None = None
    disabledReason: str | None = None


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="push_notification",
        description="Show the person a short notification (at most 180 characters), e.g. when a long task finishes or needs their attention. At most 3 per turn.",
        legacy_schema=_schema({"message": _TEXT, "status": _TEXT}, ("message",)),
        handler="push_notification", kind="user_input",
        input_model=PushNotificationInput, output_model=PushNotificationOutput,
        public_description="Show the person a short notification, e.g. when a long task finishes or needs their attention.",
        available=lambda toolset: toolset._on_notification is not None, order=1060,
    ),
)
