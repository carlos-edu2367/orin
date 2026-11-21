"""Parent-to-running-subagent messaging by ``agent_id`` with subtree isolation (WS-B7, B-RF7)."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
from typing import Any

from agentos.agentic.tools._shared import ToolOutcome
from agentos.conversations.agent_controls import MAX_CONTROL_MESSAGE_CHARS, MAX_PREVIEW_CHARS

logger = logging.getLogger("agentos.agentic.agent_messaging")

_MAX_TREE_DEPTH = 32


class AgentMessenger:
    """Validates subtree ownership & liveness and enqueues parent messages into ``AgentControlStore``."""

    def __init__(
        self,
        *,
        controls: Any,
        user_id: str,
        conversation_id: str,
        main_agent_id: str,
        agent_tree: Callable[[], Mapping[str, str]],
        is_running: Callable[[str], bool],
        is_known: Callable[[str], bool] | None = None,
        emit_event: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self._controls = controls
        self._user_id = str(user_id or "")
        self._conversation_id = str(conversation_id or "")
        self._main_agent_id = str(main_agent_id or "")
        self._agent_tree = agent_tree
        self._is_running = is_running
        self._is_known = is_known
        self._emit_event = emit_event

    def _is_descendant(self, target_agent_id: str, ancestor_agent_id: str, tree: Mapping[str, str]) -> bool:
        current = target_agent_id
        seen: set[str] = set()
        for _ in range(_MAX_TREE_DEPTH):
            if current in seen:
                return False
            seen.add(current)
            parent = str(tree.get(current) or "")
            if not parent:
                return False
            if parent == ancestor_agent_id:
                return True
            if parent == self._main_agent_id:
                return False
            current = parent
        return False

    def _target_in_subtree(self, caller_agent_id: str, target_agent_id: str) -> bool:
        tree = self._agent_tree() or {}
        if caller_agent_id == self._main_agent_id:
            if target_agent_id in tree:
                return True
            if self._is_known is not None:
                try:
                    return bool(self._is_known(target_agent_id))
                except Exception:  # noqa: BLE001
                    return False
            return False
        return self._is_descendant(target_agent_id, caller_agent_id, tree)

    def send(
        self,
        caller_agent_id: str,
        target_agent_id: str,
        message: str,
        summary: str | None = None,
    ) -> ToolOutcome:
        """Queue a direct message from ``caller_agent_id`` to running descendant ``target_agent_id``."""
        caller = str(caller_agent_id or "").strip()
        target = str(target_agent_id or "").strip()
        cleaned = str(message or "").strip()
        clean_summary = str(summary or "").strip()[:200]

        if not cleaned or len(cleaned) > MAX_CONTROL_MESSAGE_CHARS:
            return ToolOutcome(
                "failed",
                "Mensagem inválida",
                f"message must be between 1 and {MAX_CONTROL_MESSAGE_CHARS} characters.",
                {"tool_kind": "agent"},
                "INVALID_ARGUMENTS",
            )
        if not target:
            return ToolOutcome(
                "failed",
                "agent_id ausente",
                "agent_id must be a non-blank string.",
                {"tool_kind": "agent"},
                "INVALID_ARGUMENTS",
            )
        if target == caller:
            return ToolOutcome(
                "failed",
                "Mensagem para si mesmo",
                "An agent cannot send a message to itself.",
                {"agent_id": target, "tool_kind": "agent"},
                "INVALID_ARGUMENTS",
            )
        if not self._target_in_subtree(caller, target):
            return ToolOutcome(
                "failed",
                "Subagente fora da subárvore",
                f"Agent '{target[:80]}' is not a descendant of this agent; only agents you started can receive SendMessage(agent_id=...).",
                {"agent_id": target, "tool_kind": "agent"},
                "INVALID_ARGUMENTS",
            )
        if not self._is_running(target):
            return ToolOutcome(
                "failed",
                "Subagente não está em execução",
                f"Agent '{target[:80]}' is not currently running.",
                {"agent_id": target, "tool_kind": "agent"},
                "AGENT_NOT_RUNNING",
            )

        control_id = self._controls.post_message(
            self._user_id,
            self._conversation_id,
            target,
            cleaned,
            sender_kind="parent",
            sender_agent_id=caller,
        )

        # Race guard: if the target stopped between liveness check and insert, withdraw without notifying.
        if not self._is_running(target):
            withdraw = getattr(self._controls, "withdraw", None)
            if callable(withdraw) and withdraw(control_id):
                return ToolOutcome(
                    "failed",
                    "Subagente não está em execução",
                    f"Agent '{target[:80]}' finished before the message could be queued.",
                    {"agent_id": target, "tool_kind": "agent"},
                    "AGENT_NOT_RUNNING",
                )

        event_payload: dict[str, object] = {
            "control_id": control_id,
            "agent_id": target,
            "sender_kind": "parent",
            "sender_agent_id": caller,
            "preview": cleaned[:MAX_PREVIEW_CHARS],
            "state": "queued",
        }
        if clean_summary:
            event_payload["summary"] = clean_summary
        if self._emit_event is not None:
            try:
                self._emit_event("agent.message_queued", event_payload)
            except Exception:  # noqa: BLE001
                logger.debug("could not emit agent.message_queued for %s", target, exc_info=True)

        return ToolOutcome(
            "succeeded",
            f"Mensagem na fila para {target[:40]}",
            f"Queued message {control_id} for running subagent {target}. It will read it at its next safe checkpoint.",
            {
                "status": "queued",
                "control_id": control_id,
                "agent_id": target,
                "tool_kind": "agent",
            },
        )


__all__ = ["AgentMessenger"]
