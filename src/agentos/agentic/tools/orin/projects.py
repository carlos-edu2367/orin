"""Projects tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec
from agentos.conversations.code_milestone_watcher import (
    get_milestone_watcher_registry,
    sanitize_code_digest_text,
)

_ALLOWED_PERMISSION_MODES = {"accept_edits", "plan_mode", "ask_permissions"}
_ALLOWED_CODE_SESSION_ACTIONS = {
    "propose",
    "start",
    "send_message",
    "approve_or_answer",
    "status",
    "stop",
}
_ALLOWED_DECISIONS = {"approve", "reject", "revise"}


class ProjectTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def _resolve_code_session_defaults(
        self,
        *,
        workspace_path: str | None,
        provider: str | None,
        model_id: str | None,
        permission_mode: str,
    ) -> tuple[str | None, str | None, str | None, str]:
        clean_mode = str(permission_mode or "accept_edits").strip()
        if clean_mode not in _ALLOWED_PERMISSION_MODES:
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
        return effective_path, effective_provider, effective_model, clean_mode

    def _require_owned_code_conversation(self, code_conversation_id: str | None) -> tuple[str, dict[str, Any]]:
        if self._chat_store is None or not self._project_user_id:
            raise AgentToolError("Chat store is not available to control Orin Code sessions.")
        if not isinstance(code_conversation_id, str) or not (1 <= len(code_conversation_id.strip()) <= 120):
            raise AgentToolError("code_conversation_id must be a non-blank string.")
        clean_cid = code_conversation_id.strip()
        try:
            conv = self._chat_store.get(clean_cid, self._project_user_id)
        except Exception as error:
            raise AgentToolError(f"Code session '{clean_cid}' not found or access denied.") from error
        if not isinstance(conv, dict) or (
            conv.get("user_id") is not None and conv.get("user_id") != self._project_user_id
        ):
            raise AgentToolError(f"Code session '{clean_cid}' not found or access denied.")
        if conv.get("mode") is not None and conv.get("mode") != "code":
            raise AgentToolError(f"Conversation '{clean_cid}' is not an Orin Code session.")
        return clean_cid, conv

    def control_code_session(
        self,
        action: str = "start",
        title: str = "",
        prompt: str = "",
        workspace_path: str | None = None,
        provider: str | None = None,
        model_id: str | None = None,
        permission_mode: str = "accept_edits",
        code_conversation_id: str | None = None,
        message: str | None = None,
        decision: str | None = None,
        feedback: str | None = None,
    ) -> ToolOutcome:
        return self.propose_code_session(
            title=title,
            prompt=prompt,
            workspace_path=workspace_path,
            provider=provider,
            model_id=model_id,
            permission_mode=permission_mode,
            action=action,
            code_conversation_id=code_conversation_id,
            message=message,
            decision=decision,
            feedback=feedback,
        )

    def propose_code_session(
        self,
        title: str = "",
        prompt: str = "",
        workspace_path: str | None = None,
        provider: str | None = None,
        model_id: str | None = None,
        permission_mode: str = "accept_edits",
        action: str | None = None,
        code_conversation_id: str | None = None,
        message: str | None = None,
        decision: str | None = None,
        feedback: str | None = None,
    ) -> ToolOutcome:
        if action is None:
            if code_conversation_id:
                if decision is not None:
                    effective_action = "approve_or_answer"
                elif message is not None:
                    effective_action = "send_message"
                else:
                    effective_action = "status"
            else:
                effective_action = "propose"
        else:
            effective_action = str(action).strip().lower()

        if effective_action not in _ALLOWED_CODE_SESSION_ACTIONS:
            raise AgentToolError(
                f"action must be one of {sorted(_ALLOWED_CODE_SESSION_ACTIONS)}."
            )

        if effective_action in {"propose", "start"}:
            if not isinstance(title, str) or not (1 <= len(title.strip()) <= 200):
                raise AgentToolError("title must be a non-blank string between 1 and 200 characters.")
            if not isinstance(prompt, str) or not (1 <= len(prompt.strip()) <= 16000):
                raise AgentToolError("prompt must be a non-blank string between 1 and 16000 characters.")
            effective_path, effective_provider, effective_model, clean_mode = (
                self._resolve_code_session_defaults(
                    workspace_path=workspace_path,
                    provider=provider,
                    model_id=model_id,
                    permission_mode=permission_mode,
                )
            )
            if effective_action == "propose":
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

            # effective_action == "start"
            if self._chat_store is None or not self._project_user_id:
                raise AgentToolError("Chat store is not available to start an Orin Code session.")
            clean_title = title.strip()
            clean_prompt = prompt.strip()
            try:
                created = self._chat_store.create(
                    user_id=self._project_user_id,
                    message=clean_prompt,
                    provider=effective_provider,
                    model_id=effective_model,
                    idempotency_key=f"code_start_{uuid4().hex[:16]}",
                    project_id=self._project_id,
                    code_mode="code",
                    permission_mode=clean_mode,
                )
            except Exception as error:
                raise AgentToolError(f"Failed to start Orin Code session: {error}") from error

            if effective_path and getattr(self, "_local_workspaces", None) is not None:
                try:
                    self._local_workspaces.set_root(
                        f"workspace:{created.conversation_id}",
                        self._project_user_id,
                        effective_path,
                    )
                except Exception:
                    pass

            registry = getattr(self, "_milestone_registry", None) or get_milestone_watcher_registry()
            registry.register(
                friend_conversation_id=str(getattr(self, "_conversation_id", "") or ""),
                code_conversation_id=created.conversation_id,
                user_id=self._project_user_id,
                title=clean_title,
                persona=getattr(self, "_friend_persona", None),
                reminder_queue=getattr(self, "_reminders", None),
                on_ui_update=getattr(self, "_on_code_ui_progress", None),
                on_speak_chunk=getattr(self, "_on_code_speak_chunk", None),
                on_wakeup=getattr(self, "_on_code_wakeup", None),
            )
            dispatcher = getattr(self, "_code_turn_dispatcher", None)
            if callable(dispatcher):
                dispatcher(created.conversation_id, created.turn_id)

            payload = {
                "action": "start",
                "code_conversation_id": created.conversation_id,
                "turn_id": created.turn_id,
                "state": created.state,
                "title": clean_title,
                "workspace_path": effective_path,
                "provider": effective_provider,
                "model_id": effective_model,
                "permission_mode": clean_mode,
                "project_id": self._project_id,
                "wait_for_user": False,
                "tool_kind": "code_handoff",
            }
            compact_content = json.dumps(
                {
                    "code_conversation_id": created.conversation_id,
                    "turn_id": created.turn_id,
                    "state": created.state,
                    "title": clean_title,
                    "permission_mode": clean_mode,
                },
                ensure_ascii=False,
            )
            return ToolOutcome(
                "succeeded",
                f"Sessão Orin Code iniciada: {clean_title}",
                compact_content,
                payload,
            )

        if effective_action == "status":
            clean_cid, conv = self._require_owned_code_conversation(code_conversation_id)
            registry = getattr(self, "_milestone_registry", None) or get_milestone_watcher_registry()
            watcher = registry.get(clean_cid, user_id=self._project_user_id)
            if watcher is not None:
                snap = watcher.snapshot_status()
                digest_json = watcher.build_digest(
                    event_kind=str(conv.get("state") or "status"),
                ).to_json()
            else:
                turns = conv.get("turns") or []
                last_turn = turns[-1] if turns else {}
                snap = {
                    "code_conversation_id": clean_cid,
                    "friend_conversation_id": str(getattr(self, "_conversation_id", "") or ""),
                    "title": str(conv.get("title") or "")[:80],
                    "stage": str(conv.get("state") or "idle"),
                    "todos_completed": 0,
                    "todos_total": 0,
                    "active_todo": None,
                    "files_modified": [],
                    "files_read_count": 0,
                    "pending_question": None,
                    "last_error": sanitize_code_digest_text(last_turn.get("error"), max_chars=180) or None,
                    "llm_wakeups": 0,
                }
                digest_json = json.dumps(
                    {
                        "code_conversation_id": clean_cid,
                        "state": conv.get("state"),
                        "permission_mode": conv.get("permission_mode"),
                        "todos_summary": "0/0",
                        "files_touched": [],
                    },
                    ensure_ascii=False,
                )[:600]

            payload = {
                **snap,
                "action": "status",
                "state": conv.get("state"),
                "permission_mode": conv.get("permission_mode"),
                "wait_for_user": False,
                "tool_kind": "code_handoff",
            }
            return ToolOutcome(
                "succeeded",
                f"Status do Orin Code ({clean_cid}): {snap['todos_completed']}/{snap['todos_total']} etapas",
                digest_json,
                payload,
            )

        if effective_action == "send_message":
            clean_cid, conv = self._require_owned_code_conversation(code_conversation_id)
            raw_msg = message if message is not None else prompt
            if not isinstance(raw_msg, str) or not (1 <= len(raw_msg.strip()) <= 16000):
                raise AgentToolError("message must be a non-blank string between 1 and 16000 characters.")
            clean_msg = raw_msg.strip()
            try:
                created = self._chat_store.create(
                    user_id=self._project_user_id,
                    message=clean_msg,
                    provider=conv.get("provider") or self._default_provider,
                    model_id=conv.get("model_id") or self._default_model_id,
                    idempotency_key=f"code_msg_{uuid4().hex[:16]}",
                    conversation_id=clean_cid,
                    project_id=conv.get("project_id") or self._project_id,
                    code_mode="code",
                    permission_mode=conv.get("permission_mode") or "accept_edits",
                )
            except Exception as error:
                raise AgentToolError(f"Failed to send message to Orin Code session: {error}") from error

            registry = getattr(self, "_milestone_registry", None) or get_milestone_watcher_registry()
            watcher = registry.get(clean_cid, user_id=self._project_user_id)
            if watcher is not None:
                watcher.pending_question = None

            dispatcher = getattr(self, "_code_turn_dispatcher", None)
            if callable(dispatcher):
                dispatcher(clean_cid, created.turn_id)

            payload = {
                "action": "send_message",
                "code_conversation_id": clean_cid,
                "turn_id": created.turn_id,
                "state": created.state,
                "wait_for_user": False,
                "tool_kind": "code_handoff",
            }
            return ToolOutcome(
                "succeeded",
                f"Mensagem enviada para a sessão Orin Code {clean_cid}",
                json.dumps(payload, ensure_ascii=False),
                payload,
            )

        if effective_action == "approve_or_answer":
            clean_cid, conv = self._require_owned_code_conversation(code_conversation_id)
            clean_decision = str(decision or "").strip().lower()
            if clean_decision not in _ALLOWED_DECISIONS:
                raise AgentToolError("decision must be 'approve', 'reject', or 'revise'.")
            raw_feedback = feedback if feedback is not None else (message or "")
            if not isinstance(raw_feedback, str) or len(raw_feedback) > 8000:
                raise AgentToolError("feedback must be a string up to 8000 characters.")
            clean_feedback = raw_feedback.strip()

            effective_perm = str(conv.get("permission_mode") or "accept_edits")
            if clean_decision == "approve" and effective_perm == "plan_mode":
                try:
                    updated = self._chat_store.set_permission_mode(
                        clean_cid,
                        self._project_user_id,
                        "accept_edits",
                    )
                    effective_perm = str(updated.get("permission_mode") or "accept_edits")
                except Exception as error:
                    raise AgentToolError(f"Failed to update permission mode on approval: {error}") from error

            if clean_decision == "approve":
                reply_text = clean_feedback or "Plano aprovado. Pode prosseguir com a implementação."
            elif clean_decision == "revise":
                reply_text = (
                    f"Ajuste solicitado no plano: {clean_feedback}"
                    if clean_feedback
                    else "Por favor, revise o plano antes de prosseguir."
                )
            else:
                reply_text = (
                    f"Plano rejeitado: {clean_feedback}"
                    if clean_feedback
                    else "Plano rejeitado. Aguarde novas instruções."
                )

            try:
                created = self._chat_store.create(
                    user_id=self._project_user_id,
                    message=reply_text,
                    provider=conv.get("provider") or self._default_provider,
                    model_id=conv.get("model_id") or self._default_model_id,
                    idempotency_key=f"code_decision_{uuid4().hex[:16]}",
                    conversation_id=clean_cid,
                    project_id=conv.get("project_id") or self._project_id,
                    code_mode="code",
                    permission_mode=effective_perm,
                )
            except Exception as error:
                raise AgentToolError(f"Failed to submit decision to Orin Code session: {error}") from error

            registry = getattr(self, "_milestone_registry", None) or get_milestone_watcher_registry()
            watcher = registry.get(clean_cid, user_id=self._project_user_id)
            if watcher is not None:
                watcher.pending_question = None

            dispatcher = getattr(self, "_code_turn_dispatcher", None)
            if callable(dispatcher):
                dispatcher(clean_cid, created.turn_id)

            payload = {
                "action": "approve_or_answer",
                "code_conversation_id": clean_cid,
                "decision": clean_decision,
                "permission_mode": effective_perm,
                "turn_id": created.turn_id,
                "state": created.state,
                "wait_for_user": False,
                "tool_kind": "code_handoff",
            }
            return ToolOutcome(
                "succeeded",
                f"Decisão '{clean_decision}' enviada ao Orin Code ({clean_cid})",
                json.dumps(payload, ensure_ascii=False),
                payload,
            )

        # effective_action == "stop"
        clean_cid, _conv = self._require_owned_code_conversation(code_conversation_id)
        try:
            cancel_result = self._chat_store.request_cancel(clean_cid, self._project_user_id)
        except Exception as error:
            raise AgentToolError(f"Failed to stop Orin Code session: {error}") from error

        payload = {
            "action": "stop",
            "code_conversation_id": clean_cid,
            "state": cancel_result.get("state", "cancelled"),
            "cancelled_turn_ids": cancel_result.get("cancelled_turn_ids", []),
            "wait_for_user": False,
            "tool_kind": "code_handoff",
        }
        return ToolOutcome(
            "succeeded",
            f"Sessão Orin Code interrompida ({clean_cid})",
            json.dumps(payload, ensure_ascii=False),
            payload,
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


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="propose_code_session",
        description="Suggest or directly control a dedicated Orin Code session (propose, start in background, send follow-up message, approve/reject/revise plan, check compact status, or stop). In normal Chat mode action='propose' presents the approval card; in Orin Friend mode action='start' launches the Code session in background and monitors milestones.",
        legacy_schema=_schema(
            {
                "action": {
                    "type": "string",
                    "enum": ["propose", "start", "send_message", "approve_or_answer", "status", "stop"],
                    "description": "Operation to perform on the Orin Code session (default 'propose').",
                },
                "title": {**_TEXT, "description": "Short title for the new Code session."},
                "prompt": {
                    **_TEXT,
                    "description": "Self-contained task specification for the Orin Code session's first message (objective, relevant context from this chat, target files, acceptance criteria).",
                },
                "workspace_path": {
                    **_TEXT,
                    "description": "Suggested local folder path. Defaults to the conversation/project's current local folder if any.",
                },
                "provider": {
                    **_TEXT,
                    "description": "Suggested provider name. Defaults to the current conversation's provider.",
                },
                "model_id": {
                    **_TEXT,
                    "description": "Suggested model ID. Defaults to the current conversation's model.",
                },
                "permission_mode": {
                    "type": "string",
                    "enum": ["accept_edits", "plan_mode", "ask_permissions"],
                    "description": "Suggested permission mode (default 'accept_edits').",
                },
                "code_conversation_id": {
                    **_TEXT,
                    "description": "ID of an active Orin Code conversation (required for send_message, approve_or_answer, status, stop).",
                },
                "message": {
                    **_TEXT,
                    "description": "Follow-up instruction for action='send_message'.",
                },
                "decision": {
                    "type": "string",
                    "enum": ["approve", "reject", "revise"],
                    "description": "Decision for action='approve_or_answer' when Orin Code is waiting on a plan or question.",
                },
                "feedback": {
                    **_TEXT,
                    "description": "Optional rationale or revision notes for action='approve_or_answer'.",
                },
            },
            ("title", "prompt"),
        ),
        handler="propose_code_session",
        kind="code_handoff",
        policy_tags=("user_input",),
        available=lambda self: (not self._code_mode_active),
        aliases=("control_code_session", "ControlCodeSession"),
        order=550,
    ),
    ToolSpec(
        key="list_projects",
        description="List this user's active projects with their attached local folders and conversations.",
        legacy_schema=_schema({}),
        handler="list_projects",
        kind="project",
        read_only=True,
        available=lambda self: (self._project_store is not None and self._project_user_id),
        order=560,
    ),
    ToolSpec(
        key="create_project_from_session",
        description="Create a new project from the current session, attach the current workspace folder (or a specified workspace_path), and move this conversation into the new project.",
        legacy_schema=_schema(
            {
                "name": {**_TEXT, "description": "Project name (1..120 characters)."},
                "description": {**_TEXT, "description": "Optional project description."},
                "workspace_path": {
                    **_TEXT,
                    "description": "Optional local directory path to attach to the project. Defaults to the current conversation's local folder if any.",
                },
            },
            ("name",),
        ),
        handler="create_project_from_session",
        kind="project",
        policy_tags=("mutates",),
        available=lambda self: (self._project_store is not None and self._project_user_id),
        order=570,
    ),
    ToolSpec(
        key="move_session_to_project",
        description="Move the current conversation into an existing project, sharing its workspace and project memory across sessions.",
        legacy_schema=_schema(
            {
                "project_id": {**_TEXT, "description": "Target project ID (from list_projects)."},
            },
            ("project_id",),
        ),
        handler="move_session_to_project",
        kind="project",
        policy_tags=("mutates",),
        available=lambda self: (self._project_store is not None and self._project_user_id),
        order=580,
    ),
    ToolSpec(
        key="read_project_conversation",
        description="Read recent messages from another conversation belonging to this user or project so you can continue context across Chat and Code sessions.",
        legacy_schema=_schema(
            {
                "conversation_id": {**_TEXT, "description": "ID of the sibling conversation to read."},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of recent messages to read (default 20).",
                },
                "max_messages": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Alias for limit.",
                },
            },
            ("conversation_id",),
        ),
        handler="read_project_conversation",
        kind="project",
        read_only=True,
        available=lambda self: (self._chat_store is not None and self._project_user_id),
        order=590,
    ),
)
