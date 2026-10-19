"""PushNotification: tell the person something without waiting for an answer (W4).

The message becomes a ``notification.pushed`` activity event: the open Orin window shows it as a toast,
or as a system notification when the window is hidden, and it stays in the conversation's timeline. A
Notification hook is fired with it. There is no phone push in Orin, so ``pushSent`` is always false.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.lifecycle import HookEvent
from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolInput, ToolSpec

logger = logging.getLogger("agentos.agentic.agent_tools")

MAX_NOTIFICATION_CHARS = 200
MAX_NOTIFICATIONS_PER_TURN = 3


class NotifyTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def push_notification(self, message: str, status: str | None = None) -> dict[str, Any]:
        text = " ".join(str(message or "").split())
        if not text:
            raise AgentToolError("message must be a non-blank string")
        if len(text) > MAX_NOTIFICATION_CHARS:
            text = text[: MAX_NOTIFICATION_CHARS - 1].rstrip() + "…"
        sent = int(getattr(self, "_notifications_sent", 0))
        if sent >= MAX_NOTIFICATIONS_PER_TURN:
            raise AgentToolError(f"At most {MAX_NOTIFICATIONS_PER_TURN} notifications per turn; put the rest in your answer.")
        self._notifications_sent = sent + 1
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
    message: str = Field(description="What to tell the person, in one short sentence (at most 200 characters).")
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
        description="Show the person a short notification (at most 200 characters), e.g. when a long task finishes or needs their attention. At most 3 per turn.",
        legacy_schema=_schema({"message": _TEXT, "status": _TEXT}, ("message",)),
        handler="push_notification", kind="user_input",
        input_model=PushNotificationInput, output_model=PushNotificationOutput,
        public_description="Show the person a short notification, e.g. when a long task finishes or needs their attention.",
        available=lambda toolset: toolset._on_notification is not None, order=1060,
    ),
)
