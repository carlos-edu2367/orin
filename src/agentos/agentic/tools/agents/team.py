"""SendMessage: address teammates by name (``to``) or running subagents in your subtree by ``agent_id`` (W5 + WS-B7).

Mixed into ``AgentToolset``. The session wires:
- ``_team_sender`` + ``_send_team_message`` (and optional ``_has_team`` predicate) for agent teams;
- ``_send_agent_message`` + ``_has_living_children`` + ``_parent_message_tool_enabled`` for parent-to-child messaging.
"""
from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.spec import ToolInput, ToolSpec


def _parent_messaging_enabled(toolset: Any) -> bool:
    return bool(getattr(toolset, "_parent_message_tool_enabled", False)) and (
        getattr(toolset, "_send_agent_message", None) is not None
    )


def _send_message_available(toolset: Any) -> bool:
    has_team_fn = getattr(toolset, "_has_team", None)
    if callable(has_team_fn):
        has_team = bool(has_team_fn()) and getattr(toolset, "_send_team_message", None) is not None
    else:
        has_team = getattr(toolset, "_send_team_message", None) is not None
    if has_team:
        return True
    if not _parent_messaging_enabled(toolset):
        return False
    has_children_fn = getattr(toolset, "_has_living_children", None)
    return bool(has_children_fn()) if callable(has_children_fn) else False


def _send_message_schema(toolset: Any) -> dict[str, Any]:
    if _parent_messaging_enabled(toolset):
        return _schema(
            {"to": _TEXT, "agent_id": _TEXT, "message": _TEXT, "summary": _TEXT},
            ("message",),
        )
    return _schema({"to": _TEXT, "message": _TEXT, "summary": _TEXT}, ("to", "message"))


def _send_message_description(toolset: Any) -> str:
    if _parent_messaging_enabled(toolset):
        return (
            'Send a message to a teammate by name (`to`, or "lead") or to a running subagent you started (`agent_id`). '
            "Provide exactly one of `to` or `agent_id`. Running targets receive it at their next safe checkpoint; "
            "an idle teammate wakes up with it."
        )
    return (
        'Send a message to a teammate by name (or to "lead"). '
        "A running teammate receives it at its next safe checkpoint; an idle teammate starts working on it."
    )


class TeamTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def send_message(
        self,
        message: str,
        to: str | None = None,
        agent_id: str | None = None,
        summary: str | None = None,
    ) -> Any:
        clean_to = str(to or "").strip()
        clean_agent_id = str(agent_id or "").strip()
        if bool(clean_to) == bool(clean_agent_id):
            return ToolOutcome(
                "failed",
                "Destinatário inválido",
                "SendMessage requires exactly one of 'to' or 'agent_id'.",
                {"tool_kind": "agent"},
                "INVALID_ARGUMENTS",
            )
        if clean_agent_id:
            if not _parent_messaging_enabled(self):
                return ToolOutcome(
                    "failed",
                    "Envio por agent_id desabilitado",
                    "Sending messages by agent_id is disabled or unavailable here.",
                    {"agent_id": clean_agent_id, "tool_kind": "agent"},
                    "INVALID_ARGUMENTS",
                )
            send_agent = getattr(self, "_send_agent_message", None)
            if send_agent is None:
                return ToolOutcome(
                    "failed",
                    "Envio por agent_id indisponível",
                    "Sending messages by agent_id is not available on this agent.",
                    {"agent_id": clean_agent_id, "tool_kind": "agent"},
                    "INVALID_ARGUMENTS",
                )
            return send_agent(
                agent_id=clean_agent_id,
                message=str(message or ""),
                summary=str(summary or ""),
            )
        send = getattr(self, "_send_team_message", None)
        sender = getattr(self, "_team_sender", None)
        if send is None or not sender:
            raise AgentToolError(
                "There is no agent team here: start a teammate with the Agent tool (name and team_name) first."
            )
        return send(sender=str(sender), to=clean_to, message=str(message or ""), summary=str(summary or ""))


class SendMessageInput(ToolInput):
    to: str | None = Field(
        None,
        description='The teammate\'s name, or "lead" for the agent that started the team. Exclusive with agent_id.',
    )
    agent_id: str | None = Field(
        None,
        description="The agent_id of a running subagent you started (directly or indirectly). Exclusive with to.",
    )
    message: str = Field(description="What to tell them.")
    summary: str | None = Field(None, description="A few words describing the message.")

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "SendMessageInput":
        has_to = bool(str(self.to or "").strip())
        has_agent_id = bool(str(self.agent_id or "").strip())
        if has_to == has_agent_id:
            raise ValueError("Provide exactly one of 'to' or 'agent_id'.")
        return self


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="send_message",
        description=_send_message_description,
        legacy_schema=_send_message_schema,
        handler="send_message",
        kind="agent",
        input_model=SendMessageInput,
        public_description=(
            'Send a message to a teammate by name (`to`, or "lead") or to a running subagent you started (`agent_id`). '
            "Provide exactly one of `to` or `agent_id`. Running targets receive it at their next safe checkpoint; "
            "an idle teammate starts on it."
        ),
        available=_send_message_available,
        order=395,
    ),
)

__all__ = ["SPECS", "SendMessageInput", "TeamTools"]
