"""Projects tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4
from agentos.agentic.tools._shared import (
    AgentToolError,
    ToolOutcome,
)


class ProjectTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def propose_code_session(
        self,
        title: str,
        prompt: str,
        workspace_path: str | None = None,
        provider: str | None = None,
        model_id: str | None = None,
        permission_mode: str = "accept_edits",
    ) -> ToolOutcome:
        if not isinstance(title, str) or not (1 <= len(title.strip()) <= 200):
            raise AgentToolError("title must be a non-blank string between 1 and 200 characters.")
        if not isinstance(prompt, str) or not (1 <= len(prompt.strip()) <= 16000):
            raise AgentToolError("prompt must be a non-blank string between 1 and 16000 characters.")
        clean_mode = str(permission_mode or "accept_edits").strip()
        if clean_mode not in {"accept_edits", "plan_mode", "ask_permissions"}:
            raise AgentToolError(
                "permission_mode must be 'accept_edits', 'plan_mode', or 'ask_permissions'. 'full_autonomy' cannot be proposed automatically."
            )
        effective_path = (
            workspace_path.strip()
            if isinstance(workspace_path, str) and workspace_path.strip()
            else self._default_workspace_path
        )
        _PLACEHOLDER_MODELS = {"default", "auto", "current", "none", "null"}
        clean_model = model_id.strip() if isinstance(model_id, str) else ""
        if clean_model.lower() in _PLACEHOLDER_MODELS:
            clean_model = ""
        clean_provider = provider.strip().lower() if isinstance(provider, str) else ""
        if self._default_provider and (not clean_provider or not clean_model):
            effective_provider = self._default_provider
        else:
            effective_provider = clean_provider or self._default_provider
        effective_model = (
            clean_model
            if (clean_model and effective_provider == (clean_provider or self._default_provider))
            else (self._default_model_id or clean_model or None)
        )
        proposal = {
            "proposal_id": f"codeprop_{uuid4().hex[:12]}",
            "title": title.strip(),
            "prompt": prompt.strip(),
            "workspace_path": effective_path,
            "provider": effective_provider,
            "model_id": effective_model,
            "permission_mode": clean_mode,
            "project_id": self._project_id,
        }
        return ToolOutcome(
            "succeeded",
            f"Sugestão para iniciar no Orin Code: {proposal['title']}",
            "Presented the 'Start in Orin Code' card to the user. Stop here and wait for the user's decision.",
            {
                "code_session_proposal": proposal,
                "wait_for_user": True,
                "tool_kind": "code_handoff",
            },
        )

    def list_projects(self) -> dict[str, Any]:
        if self._project_store is None or not self._project_user_id:
            raise AgentToolError("Project store is not available.")
        items = self._project_store.sidebar(self._project_user_id)
        serialized: list[dict[str, Any]] = []
        for item in items:
            ws_path = None
            if self._local_workspaces is not None:
                ws_id = getattr(item, "workspace_id", "") or f"workspace:{item.project_id}"
                ws_path = self._local_workspaces.root_for(ws_id, self._project_user_id)
            serialized.append(
                {
                    "project_id": item.project_id,
                    "name": item.name,
                    "description": item.description,
                    "workspace_path": ws_path,
                    "chats": [
                        {
                            "conversation_id": c.conversation_id,
                            "title": c.title,
                            "mode": getattr(c, "mode", "chat"),
                        }
                        for c in item.chats
                    ],
                }
            )
        return {
            "summary": f"{len(serialized)} projeto(s) encontrado(s)",
            "content": json.dumps(serialized, ensure_ascii=False),
            "payload": {"projects": serialized, "tool_kind": "project", "project_action": "list"},
        }

    def create_project_from_session(
        self,
        name: str,
        description: str = "",
        workspace_path: str | None = None,
    ) -> dict[str, Any]:
        if self._project_store is None or not self._project_user_id:
            raise AgentToolError("Project store is not available.")
        if not isinstance(name, str):
            raise AgentToolError("Project name must be a non-blank string.")
        normalized_name = " ".join(name.split())
        if not normalized_name or len(normalized_name) > 120:
            raise AgentToolError("Project name must be a non-blank string between 1 and 120 characters.")
        explicit_ws = (
            workspace_path.strip()
            if isinstance(workspace_path, str) and workspace_path.strip()
            else None
        )
        effective_ws = explicit_ws or self._default_workspace_path
        resolved_ws_path: str | None = None
        if effective_ws:
            from agentos.installation import orin_paths
            from agentos.local_workspace import FolderRejected, inspect_folder

            try:
                info = inspect_folder(effective_ws, home=Path.home(), orin_data=orin_paths().data)
            except FolderRejected as error:
                raise AgentToolError(str(error)) from error
            if not info.exists or not info.is_directory or not info.writable:
                raise AgentToolError("workspace_path must be an existing writable directory.")
            resolved_ws_path = info.path
        try:
            record = self._project_store.create(
                user_id=self._project_user_id,
                name=normalized_name,
                description=str(description).strip() or None if description else None,
            )
        except Exception as error:
            raise AgentToolError(str(error)) from error
        if resolved_ws_path and self._local_workspaces is not None:
            self._local_workspaces.set_root(record.workspace_id, self._project_user_id, resolved_ws_path)
        moved = None
        if self._chat_store is not None and self._conversation_id:
            try:
                moved = self._chat_store.set_project(
                    self._conversation_id,
                    self._project_user_id,
                    record.project_id,
                )
            except Exception as error:
                raise AgentToolError(str(error)) from error
            if resolved_ws_path is None and self._local_workspaces is not None:
                resolved_ws_path = self._local_workspaces.root_for(record.workspace_id, self._project_user_id)
        self._project_id = record.project_id
        payload = {
            "project_id": record.project_id,
            "name": record.name,
            "description": record.description,
            "workspace_path": resolved_ws_path,
            "conversation_id": self._conversation_id,
            "moved": moved,
            "tool_kind": "project",
            "project_action": "created",
        }
        return {
            "summary": f"Criou o projeto {record.name}",
            "content": json.dumps(payload, ensure_ascii=False),
            "payload": payload,
        }

    def move_session_to_project(self, project_id: str) -> dict[str, Any]:
        if self._project_store is None or not self._project_user_id:
            raise AgentToolError("Project store is not available.")
        if not self._conversation_id or self._chat_store is None:
            raise AgentToolError("Cannot move session: no active conversation context.")
        clean_pid = str(project_id or "").strip()
        if not clean_pid:
            raise AgentToolError("project_id is required.")
        project = self._project_store.get(clean_pid, self._project_user_id)
        if project is None:
            raise AgentToolError(f"Project '{clean_pid}' not found.")
        try:
            result = self._chat_store.set_project(
                self._conversation_id,
                self._project_user_id,
                project.project_id,
            )
        except Exception as error:
            raise AgentToolError(str(error)) from error
        self._project_id = project.project_id
        ws_path = (
            self._local_workspaces.root_for(project.workspace_id, self._project_user_id)
            if self._local_workspaces is not None
            else None
        )
        payload = {
            **result,
            "workspace_path": ws_path,
            "tool_kind": "project",
            "project_action": "moved",
        }
        return {
            "summary": f"Moveu a sessão para o projeto {project.name}",
            "content": json.dumps(payload, ensure_ascii=False),
            "payload": payload,
        }

    def read_project_conversation(
        self,
        conversation_id: str,
        limit: int = 20,
        max_messages: int | None = None,
    ) -> dict[str, Any]:
        if self._chat_store is None or not self._project_user_id:
            raise AgentToolError("Chat store is not available.")
        clean_cid = str(conversation_id or "").strip()
        if not clean_cid:
            raise AgentToolError("conversation_id is required.")
        try:
            effective_limit = min(max(int(max_messages if max_messages is not None else limit), 1), 50)
        except (TypeError, ValueError) as error:
            raise AgentToolError("limit must be an integer between 1 and 50.") from error
        try:
            result = self._chat_store.read_project_conversation(
                clean_cid,
                self._project_user_id,
                limit=effective_limit,
            )
        except Exception as error:
            raise AgentToolError(f"Conversation '{clean_cid}' not found or not accessible.") from error
        return {
            "summary": f"Leu conversa {result.get('title', clean_cid)}",
            "content": json.dumps(result, ensure_ascii=False),
            "payload": {
                "conversation_id": clean_cid,
                "title": result.get("title"),
                "tool_kind": "project",
                "project_action": "read_conversation",
            },
        }
