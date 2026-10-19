"""Todo tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from typing import Any, Mapping, Literal

from pydantic import Field

from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec, ToolInput


class TodoTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def todo_write(self, todos: list[Mapping[str, Any]]) -> dict[str, Any]:
        if not isinstance(todos, list):
            raise AgentToolError("todos must be a list of task objects.")
        if len(todos) > 40:
            raise AgentToolError("todos cannot exceed 40 items.")
        normalized: list[dict[str, Any]] = []
        completed_count = 0
        in_progress_count = 0
        pending_count = 0
        lines: list[str] = []
        for index, raw_item in enumerate(todos):
            if not isinstance(raw_item, Mapping):
                raise AgentToolError(f"todos[{index}] must be an object.")
            raw_id = raw_item.get("id")
            item_id = (str(raw_id).strip() if raw_id is not None and str(raw_id).strip() else f"todo_{index + 1}")[:64]
            content = str(raw_item.get("content") or raw_item.get("subject") or "").strip()[:300]
            status = str(raw_item.get("status") or "pending").strip().lower()
            active_form = str(raw_item.get("active_form") or raw_item.get("activeForm") or "").strip()[:200]
            if not item_id or not content:
                raise AgentToolError(f"todos[{index}] requires non-blank 'content'.")
            if status not in {"pending", "in_progress", "completed"}:
                raise AgentToolError(
                    f"todos[{index}] status must be 'pending', 'in_progress', or 'completed' (got '{status}')."
                )
            entry: dict[str, Any] = {"id": item_id, "content": content, "status": status}
            if active_form:
                entry["active_form"] = active_form
            normalized.append(entry)
            if status == "completed":
                completed_count += 1
                marker = "[x]"
            elif status == "in_progress":
                in_progress_count += 1
                marker = "[~]"
            else:
                pending_count += 1
                marker = "[ ]"
            suffix = f" ({active_form})" if status == "in_progress" and active_form else ""
            lines.append(f"- {marker} {item_id}: {content}{suffix}")
        self._todos.clear()
        self._todos.extend(normalized)
        if callable(self._on_todo_update):
            self._on_todo_update(list(self._todos))
        summary = f"Lista de tarefas ({completed_count}/{len(normalized)} concluídas)"
        body = "Updated todo list:\n" + ("\n".join(lines) if lines else "[empty todo list]")
        return {
            "summary": summary,
            "content": body,
            "todos": list(self._todos),
            "total": len(normalized),
            "completed": completed_count,
            "in_progress": in_progress_count,
            "pending": pending_count,
            "payload": {
                "todos": list(self._todos),
                "total": len(normalized),
                "completed": completed_count,
                "in_progress": in_progress_count,
                "pending": pending_count,
                "tool_kind": "planning",
            },
        }

    def complete_remaining_todos(self, *, failed: bool = False, state: str | None = None) -> list[dict[str, Any]] | None:
        """Mark any remaining pending or in_progress tasks as completed when a turn finishes successfully."""
        if failed or (state is not None and str(state).lower() in {"failed", "cancelled", "blocked", "error"}):
            return None
        if not self._todos:
            return None
        if not any(str(item.get("status") or "") != "completed" for item in self._todos):
            return None
        for item in self._todos:
            if str(item.get("status") or "") != "completed":
                item["status"] = "completed"
        if callable(self._on_todo_update):
            self._on_todo_update(list(self._todos))
        return [dict(item) for item in self._todos]


class TodoItem(ToolInput):
    content: str = Field(description="The task, in imperative form.")
    status: Literal["pending", "in_progress", "completed"]
    activeForm: str = Field(description="Present-continuous label shown while in progress.")


class TodoWriteInput(ToolInput):
    todos: list[TodoItem] = Field(max_length=40)


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="todo_write",
        description="Create or update the structured task checklist for the current session. Use this to track progress on multi-step implementations (pending, in_progress, completed). Keep at most one task in_progress at a time.",
        legacy_schema=_schema({
            "todos": {
                "type": "array",
                "minItems": 0,
                "maxItems": 40,
                "items": _schema({
                    "id": {**_TEXT, "description": "Optional short identifier, e.g. '1' or 'auth-middleware' (auto-generated when omitted)."},
                    "content": {**_TEXT, "description": "Task description in imperative form."},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                    "active_form": {**_TEXT, "description": "Optional present-continuous label shown while in_progress."},
                    "activeForm": {**_TEXT, "description": "Alias for active_form."},
                }, ("content", "status")),
            },
        }, ("todos",)),
        handler="todo_write",
        kind="planning",
        input_model=TodoWriteInput,
        public_description="Write the session's todo list. Keep exactly one item in_progress while you work.",
        read_only=True,
        order=140,
    ),
)
