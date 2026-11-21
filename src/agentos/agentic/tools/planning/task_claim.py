"""TaskClaim tool: atomic claim on the conversation's shared task board (WS-B3, B-RF3)."""
from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from agentos.agentic.agent_context import get_current_agent_id
from agentos.agentic.task_board import record_to_task_dict
from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.spec import ToolInput, ToolSpec


def _task_claim_available(toolset: Any) -> bool:
    return getattr(toolset, "_task_board", None) is not None and bool(
        getattr(toolset, "_task_board_enabled", False)
    )


class TaskClaimTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def _task_board_actor_id(self) -> str:
        explicit = str(getattr(self, "_task_board_agent_id", None) or "").strip()
        if explicit:
            return explicit[:64]
        lifecycle_ctx = getattr(self, "_lifecycle_context", None)
        if isinstance(lifecycle_ctx, dict):
            from_ctx = str(lifecycle_ctx.get("agent_id") or "").strip()
            if from_ctx:
                return from_ctx[:64]
        from_var = str(get_current_agent_id() or "").strip()
        if from_var:
            return from_var[:64]
        return "agent:main"

    def _touch_task_board_lease(self) -> None:
        board = getattr(self, "_task_board", None)
        if board is None or not bool(getattr(self, "_task_board_enabled", False)):
            return
        renew = getattr(board, "renew_for_agent", None)
        if callable(renew):
            try:
                renew(self._task_board_actor_id())
            except Exception:  # noqa: BLE001 - lease renewal is best-effort and never breaks a tool call
                pass

    def task_claim(
        self,
        task_id: str = "",
        taskId: str = "",
        id: str = "",
        lease_seconds: int | None = None,
    ) -> ToolOutcome | dict[str, Any]:
        board = getattr(self, "_task_board", None)
        if board is None or not bool(getattr(self, "_task_board_enabled", False)):
            raise AgentToolError("Shared task board is not enabled for this conversation.")
        target_id = str(task_id or taskId or id or "").strip()
        if not target_id:
            return ToolOutcome(
                "failed",
                "task_id obrigatório",
                "task_id (or taskId) is required to claim a task.",
                {"tool_kind": "planning", "claimed": False},
                "INVALID_ARGUMENTS",
            )
        actor_id = self._task_board_actor_id()
        claimed = board.claim(target_id, actor_id, lease_seconds=lease_seconds)
        if claimed is None:
            error_code, message = board.explain_claim_failure(target_id, actor_id)
            return ToolOutcome(
                "failed",
                message[:240],
                message,
                {"tool_kind": "planning", "task_id": target_id, "claimed": False},
                error_code,
            )
        task_dict = record_to_task_dict(claimed)
        return {
            "summary": f"Tarefa reivindicada: {claimed.item_id} ({claimed.title[:60]})",
            "content": json.dumps(task_dict, ensure_ascii=False, indent=2),
            "claimed": True,
            "task": task_dict,
            "payload": {
                "claimed": True,
                "task": task_dict,
                "tool_kind": "planning",
            },
        }


class TaskClaimInput(ToolInput):
    taskId: str = Field(description="ID of the pending task to claim atomically from the shared board.")


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="task_claim",
        description=(
            "Atomically claim a pending, unblocked task from the conversation's shared task board "
            "for the current agent (15-minute renewable lease)."
        ),
        legacy_schema=_schema({
            "taskId": {**_TEXT, "description": "ID of the task to claim."},
            "task_id": {**_TEXT, "description": "Alias for taskId."},
            "id": {**_TEXT, "description": "Alias for taskId."},
        }),
        handler="task_claim",
        kind="planning",
        input_model=TaskClaimInput,
        public_description="Atomically claim a pending, unblocked task from the conversation's shared task board.",
        read_only=True,
        available=_task_claim_available,
        order=185,
    ),
)
