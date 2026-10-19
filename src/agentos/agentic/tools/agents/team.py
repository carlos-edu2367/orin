"""SendMessage: the lead and its teammates write to each other (W5 agent teams).

Mixed into ``AgentToolset``. The session sets ``self._team_sender`` (``"lead"`` on the main toolset, the teammate's
name on a teammate's toolset) and ``self._send_team_message`` (the session's delivery); without them the tool is not
offered.
"""
from __future__ import annotations

from typing import Any

from pydantic import Field

from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolInput, ToolSpec


class TeamTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def send_message(self, to: str, message: str, summary: str | None = None) -> Any:
        send = getattr(self, "_send_team_message", None)
        sender = getattr(self, "_team_sender", None)
        if send is None or not sender:
            raise AgentToolError("There is no agent team here: start a teammate with the Agent tool (name and team_name) first.")
        return send(sender=str(sender), to=str(to or ""), message=str(message or ""), summary=str(summary or ""))


class SendMessageInput(ToolInput):
    to: str = Field(description="The teammate's name, or \"lead\" for the agent that started the team.")
    message: str = Field(description="What to tell them.")
    summary: str | None = Field(None, description="A few words describing the message.")


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="send_message",
        description="Send a message to a teammate by name (or to \"lead\"). An idle teammate starts working on it; a busy one reads it when it finishes its current task.",
        legacy_schema=_schema({"to": _TEXT, "message": _TEXT, "summary": _TEXT}, ("to", "message")),
        handler="send_message",
        kind="agent",
        input_model=SendMessageInput,
        public_description="Send a message to a teammate by name, or to \"lead\". An idle teammate starts on it; a busy one reads it after its current task.",
        available=lambda toolset: getattr(toolset, "_send_team_message", None) is not None,
        order=395,
    ),
)

__all__ = ["SPECS", "SendMessageInput", "TeamTools"]
