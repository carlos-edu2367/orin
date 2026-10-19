"""Tasks tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from typing import Any, Mapping, Literal

from pydantic import Field

from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec, ToolInput


class TaskTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def _next_task_id(self) -> str:
        existing_ids = {str(item.get("id", "")) for item in self._todos}
        counter = len(self._todos) + 1
        while f"todo_{counter}" in existing_ids or str(counter) in existing_ids:
            counter += 1
        return f"todo_{counter}"

    def task_create(
        self,
        subject: str = "",
        description: str = "",
        content: str = "",
        active_form: str = "",
        status: str = "pending",
        owner: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        task_subject = str(subject or content or "").strip()[:300]
        if not task_subject:
            raise AgentToolError("subject (or content) is required to create a task.")
        clean_status = str(status or "pending").strip().lower()
        if clean_status not in {"pending", "in_progress", "completed"}:
            raise AgentToolError("status must be 'pending', 'in_progress', or 'completed'.")
        task_id = self._next_task_id()
        entry: dict[str, Any] = {
            "id": task_id,
            "subject": task_subject,
            "content": task_subject,
            "description": str(description or "").strip()[:1000],
            "status": clean_status,
        }
        clean_active = str(active_form or "").strip()[:200]
        if clean_active:
            entry["active_form"] = clean_active
        if owner is not None and str(owner).strip():
            entry["owner"] = str(owner).strip()[:120]
        if isinstance(metadata, Mapping) and metadata:
            entry["metadata"] = {
                str(k)[:80]: v for k, v in list(metadata.items())[:16] if v is not None
            }
        self._todos.append(entry)
        if callable(self._on_todo_update):
            self._on_todo_update(list(self._todos))
        return {
            "summary": f"Tarefa criada: {task_id} ({task_subject[:60]})",
            "content": json.dumps(entry, ensure_ascii=False, indent=2),
            "task": dict(entry),
            "todos": list(self._todos),
            "payload": {
                "task": dict(entry),
                "todos": list(self._todos),
                "tool_kind": "planning",
            },
        }

    def task_update(
        self,
        task_id: str,
        status: str | None = None,
        subject: str | None = None,
        description: str | None = None,
        active_form: str | None = None,
        owner: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        add_blocked_by: list[str] | None = None,
        add_blocks: list[str] | None = None,
    ) -> dict[str, Any]:
        target_id = str(task_id or "").strip()
        if not target_id:
            raise AgentToolError("task_id is required.")
        found: dict[str, Any] | None = None
        for item in self._todos:
            if str(item.get("id", "")) == target_id or (
                target_id.isdigit() and str(item.get("id", "")) == f"todo_{target_id}"
            ):
                found = item
                break
        if found is None:
            raise AgentToolError(f"Task '{target_id}' not found.")
        if status is not None:
            clean_status = str(status).strip().lower()
            if clean_status == "deleted":
                self._todos.remove(found)
                if callable(self._on_todo_update):
                    self._on_todo_update(list(self._todos))
                deleted_entry = {**found, "status": "deleted"}
                return {
                    "summary": f"Tarefa removida: {found.get('id')}",
                    "content": json.dumps(deleted_entry, ensure_ascii=False, indent=2),
                    "task": deleted_entry,
                    "todos": list(self._todos),
                    "payload": {
                        "task": deleted_entry,
                        "todos": list(self._todos),
                        "tool_kind": "planning",
                    },
                }
            if clean_status not in {"pending", "in_progress", "completed"}:
                raise AgentToolError("status must be 'pending', 'in_progress', 'completed', or 'deleted'.")
            found["status"] = clean_status
        if subject is not None and str(subject).strip():
            clean_subject = str(subject).strip()[:300]
            found["subject"] = clean_subject
            found["content"] = clean_subject
        if description is not None:
            found["description"] = str(description).strip()[:1000]
        if active_form is not None:
            found["active_form"] = str(active_form).strip()[:200]
        if owner is not None:
            found["owner"] = str(owner).strip()[:120]
        if isinstance(metadata, Mapping):
            merged_meta = dict(found.get("metadata") or {})
            for k, v in list(metadata.items())[:16]:
                key_str = str(k)[:80]
                if v is None:
                    merged_meta.pop(key_str, None)
                else:
                    merged_meta[key_str] = v
            found["metadata"] = merged_meta
        if add_blocked_by:
            existing_blocked = list(found.get("blocked_by") or [])
            for dep in add_blocked_by:
                dep_id = str(dep).strip()
                if dep_id and dep_id not in existing_blocked:
                    existing_blocked.append(dep_id)
            found["blocked_by"] = existing_blocked
        if add_blocks:
            existing_blocks = list(found.get("blocks") or [])
            for dep in add_blocks:
                dep_id = str(dep).strip()
                if dep_id and dep_id not in existing_blocks:
                    existing_blocks.append(dep_id)
            found["blocks"] = existing_blocks
        if callable(self._on_todo_update):
            self._on_todo_update(list(self._todos))
        return {
            "summary": f"Tarefa atualizada: {found.get('id')} -> {found.get('status')}",
            "content": json.dumps(found, ensure_ascii=False, indent=2),
            "task": dict(found),
            "todos": list(self._todos),
            "payload": {
                "task": dict(found),
                "todos": list(self._todos),
                "tool_kind": "planning",
            },
        }

    def task_get(self, task_id: str) -> dict[str, Any]:
        target_id = str(task_id or "").strip()
        if not target_id:
            raise AgentToolError("task_id is required.")
        for item in self._todos:
            if str(item.get("id", "")) == target_id or (
                target_id.isdigit() and str(item.get("id", "")) == f"todo_{target_id}"
            ):
                return {
                    "summary": f"Tarefa {item.get('id')}: {item.get('status')}",
                    "content": json.dumps(item, ensure_ascii=False, indent=2),
                    "task": dict(item),
                    "payload": {
                        "task": dict(item),
                        "tool_kind": "planning",
                    },
                }
        raise AgentToolError(f"Task '{target_id}' not found.")

    def task_list(self, status: str | None = None) -> dict[str, Any]:
        clean_status = str(status).strip().lower() if status is not None and str(status).strip() else None
        tasks = [
            dict(item)
            for item in self._todos
            if clean_status is None or str(item.get("status", "")).lower() == clean_status
        ]
        return {
            "summary": f"{len(tasks)} tarefa(s)",
            "content": json.dumps(tasks, ensure_ascii=False, indent=2),
            "tasks": tasks,
            "count": len(tasks),
            "total": len(tasks),
            "payload": {
                "tasks": [dict(item) for item in tasks[:6]],
                "todos": tasks,
                "count": len(tasks),
                "total": len(tasks),
                "tool_kind": "planning",
            },
        }


class TaskCreateInput(ToolInput):
    subject: str = Field(description="Short imperative title.")
    description: str = Field(description="What the task involves.")
    activeForm: str | None = Field(None, description="Present-continuous label shown while in progress.")
    metadata: dict[str, Any] | None = None


class TaskUpdateInput(ToolInput):
    taskId: str
    status: Literal["pending", "in_progress", "completed", "deleted"] | None = None
    subject: str | None = None
    description: str | None = None
    activeForm: str | None = None
    owner: str | None = None
    addBlocks: list[str] | None = None
    addBlockedBy: list[str] | None = None
    metadata: dict[str, Any] | None = None


class TaskGetInput(ToolInput):
    taskId: str


class TaskListInput(ToolInput):
    pass


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="task_create",
        description="Create a structured task in the session task list.",
        legacy_schema=_schema({
            "subject": {**_TEXT, "description": "Brief imperative title for the task."},
            "content": {**_TEXT, "description": "Alias for subject."},
            "description": {**_TEXT, "description": "Detailed description of what the task entails."},
            "activeForm": {**_TEXT, "description": "Present continuous label shown while in_progress."},
            "active_form": {**_TEXT, "description": "Alias for activeForm."},
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
            "owner": {**_TEXT, "description": "Optional owner/agent name."},
            "metadata": {"type": "object", "additionalProperties": True},
        }, ("subject",)),
        handler="task_create",
        kind="planning",
        input_model=TaskCreateInput,
        public_description='Create a task in the session task list.',
        read_only=True,
        order=150,
    ),
    ToolSpec(
        key="task_update",
        description="Update an existing task in the session task list by ID (status, subject, description, dependencies, or delete).",
        legacy_schema=_schema({
            "taskId": {**_TEXT, "description": "ID of the task to update."},
            "task_id": {**_TEXT, "description": "Alias for taskId."},
            "id": {**_TEXT, "description": "Alias for taskId."},
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "deleted"]},
            "subject": {**_TEXT, "description": "Updated task subject."},
            "description": {**_TEXT, "description": "Updated task description."},
            "activeForm": {**_TEXT, "description": "Updated present-continuous activeForm."},
            "active_form": {**_TEXT, "description": "Alias for activeForm."},
            "owner": {**_TEXT, "description": "Optional owner/agent name."},
            "addBlocks": {"type": "array", "items": _TEXT},
            "add_blocks": {"type": "array", "items": _TEXT},
            "addBlockedBy": {"type": "array", "items": _TEXT},
            "add_blocked_by": {"type": "array", "items": _TEXT},
            "metadata": {"type": "object", "additionalProperties": True},
        }),
        handler="task_update",
        kind="planning",
        input_model=TaskUpdateInput,
        public_description='Update a task: status, text, ownership or dependencies.',
        read_only=True,
        order=160,
    ),
    ToolSpec(
        key="task_get",
        description="Retrieve full details of a task by ID from the session task list.",
        legacy_schema=_schema({
            "taskId": {**_TEXT, "description": "ID of the task to retrieve."},
            "task_id": {**_TEXT, "description": "Alias for taskId."},
            "id": {**_TEXT, "description": "Alias for taskId."},
        }),
        handler="task_get",
        kind="planning",
        input_model=TaskGetInput,
        public_description='Get one task by id.',
        read_only=True,
        order=170,
    ),
    ToolSpec(
        key="task_list",
        description="List all tasks in the session task list.",
        legacy_schema=_schema({
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed"],
                "description": "Optional status filter.",
            },
        }),
        handler="task_list",
        kind="planning",
        input_model=TaskListInput,
        public_description="List the session's tasks.",
        read_only=True,
        order=180,
    ),
)
