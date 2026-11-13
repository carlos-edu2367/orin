"""Projects tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec
from agentos.conversations.code_milestone_watcher import (
    get_milestone_watcher_registry,
    sanitize_code_digest_text,
)


def _known_permission_mode(value: object) -> str:
    from agentos.code_mode.models import known_permission_mode

    return known_permission_mode(value)


def _public_permission_mode(value: str) -> str:
    from agentos.code_mode.models import public_permission_mode

    return public_permission_mode(value)

_ALLOWED_PERMISSION_MODES = {"accept_edits", "plan_mode", "ask_permissions"}
_MANAGE_SESSION_PERMISSION_MODES = {"full_autonomy", "accept_edits", "plan_mode", "ask_permissions"}
_ALLOWED_CODE_SESSION_ACTIONS = {
    "propose",
    "start",
    "send_message",
    "approve_or_answer",
    "status",
    "stop",
}
_ALLOWED_MANAGE_SESSION_ACTIONS = {
    "list",
    "inspect",
    "start",
    "send_message",
    "respond",
    "stop",
}
_ALLOWED_DECISIONS = {"approve", "reject", "revise"}
_ALLOWED_PERMISSION_DECISIONS = {"allow_once", "allow_always", "deny"}
_PLACEHOLDER_MODELS = {"default", "auto", "current", "none", "null"}


class ProjectTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def _catalog_entries_for_resolution(self) -> list[dict[str, Any]]:
        collector = getattr(self, "_collect_user_catalog_models", None)
        if not callable(collector):
            return []
        try:
            items = list(collector() or [])
        except Exception:
            return []
        has_explicit_source = (
            getattr(self, "_provider_catalog", None) is not None
            or getattr(getattr(self, "_chat_store", None), "_engine", None) is not None
            or getattr(getattr(self, "_project_store", None), "_engine", None) is not None
        )
        if not has_explicit_source:
            return []
        return [item for item in items if isinstance(item, dict)]

    def _resolve_session_defaults(
        self,
        *,
        workspace_path: str | None,
        provider: str | None,
        model_id: str | None,
        permission_mode: str | None,
        allow_full_autonomy: bool = True,
        default_permission_mode: str = "accept_edits",
    ) -> tuple[str | None, str | None, str | None, str]:
        raw_perm = str(permission_mode or default_permission_mode).strip()
        clean_mode = _known_permission_mode(raw_perm) if allow_full_autonomy else raw_perm
        if allow_full_autonomy:
            if clean_mode not in _MANAGE_SESSION_PERMISSION_MODES:
                raise AgentToolError(
                    "permission_mode must be 'full_autonomy', 'accept_edits', 'plan_mode', or 'ask_permissions'."
                )
        else:
            if clean_mode not in _ALLOWED_PERMISSION_MODES:
                raise AgentToolError(
                    "permission_mode must be 'accept_edits', 'plan_mode', or 'ask_permissions'. 'full_autonomy' cannot be proposed automatically."
                )

        effective_path = (
            workspace_path.strip()
            if isinstance(workspace_path, str) and workspace_path.strip()
            else self._default_workspace_path
        )

        raw_model = model_id.strip() if isinstance(model_id, str) else ""
        was_placeholder_model = bool(raw_model) and raw_model.lower() in _PLACEHOLDER_MODELS
        clean_model = "" if was_placeholder_model else raw_model

        raw_provider = provider.strip().lower() if isinstance(provider, str) else ""
        clean_provider = "" if raw_provider in _PLACEHOLDER_MODELS else raw_provider

        catalog_items = self._catalog_entries_for_resolution()
        default_prov = str(self._default_provider or "").strip().lower() or None
        default_mod = str(self._default_model_id or "").strip() or None

        if clean_provider and clean_model:
            effective_provider: str | None = clean_provider
            prov_items = [m for m in catalog_items if m.get("provider") == clean_provider]
            matched_item = next(
                (
                    m
                    for m in prov_items
                    if str(m.get("model_id") or "").lower() == clean_model.lower()
                    or str(m.get("display_name") or "").lower() == clean_model.lower()
                ),
                None,
            )
            effective_model: str | None = str(matched_item["model_id"]) if matched_item else clean_model
            return effective_path, effective_provider, effective_model, clean_mode

        if clean_provider and not clean_model:
            prov_items = [m for m in catalog_items if m.get("provider") == clean_provider]
            if prov_items:
                fav = next((m for m in prov_items if m.get("is_favorite")), prov_items[0])
                return effective_path, clean_provider, str(fav["model_id"]), clean_mode
            if was_placeholder_model and default_prov and default_mod:
                return effective_path, default_prov, default_mod, clean_mode
            if clean_provider == default_prov and default_mod:
                return effective_path, clean_provider, default_mod, clean_mode
            try:
                from agentos.provider_catalog.cli_providers import cli_adapter_for

                adapter = cli_adapter_for(clean_provider)
                if adapter is not None:
                    cli_models = list(adapter.models() or ())
                    if cli_models:
                        first_id = str(getattr(cli_models[0], "id", "") or "").strip()
                        if first_id:
                            return effective_path, clean_provider, first_id, clean_mode
            except Exception:
                pass
            return effective_path, clean_provider, default_mod, clean_mode

        if not clean_provider and clean_model:
            if default_prov:
                default_prov_items = [m for m in catalog_items if m.get("provider") == default_prov]
                matched_default = next(
                    (
                        m
                        for m in default_prov_items
                        if str(m.get("model_id") or "").lower() == clean_model.lower()
                        or str(m.get("display_name") or "").lower() == clean_model.lower()
                    ),
                    None,
                )
                if matched_default is not None:
                    return effective_path, default_prov, str(matched_default["model_id"]), clean_mode
            matches = [
                m
                for m in catalog_items
                if str(m.get("model_id") or "").lower() == clean_model.lower()
                or str(m.get("display_name") or "").lower() == clean_model.lower()
            ]
            if matches:
                chosen = next((m for m in matches if m.get("is_favorite")), matches[0])
                return effective_path, str(chosen["provider"]), str(chosen["model_id"]), clean_mode
            return effective_path, default_prov, clean_model, clean_mode

        return effective_path, default_prov, default_mod, clean_mode

    def _resolve_code_session_defaults(
        self,
        *,
        workspace_path: str | None,
        provider: str | None,
        model_id: str | None,
        permission_mode: str,
    ) -> tuple[str | None, str | None, str | None, str]:
        return self._resolve_session_defaults(
            workspace_path=workspace_path,
            provider=provider,
            model_id=model_id,
            permission_mode=permission_mode,
            allow_full_autonomy=False,
            default_permission_mode="accept_edits",
        )

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

            if clean_title and callable(getattr(self._chat_store, "rename", None)):
                try:
                    self._chat_store.rename(created.conversation_id, self._project_user_id, clean_title)
                except Exception:
                    pass

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
            self._notify_session_watch(
                target_conversation_id=created.conversation_id,
                target_turn_id=created.turn_id,
                origin="friend",
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

            self._notify_session_watch(
                target_conversation_id=clean_cid,
                target_turn_id=created.turn_id,
                origin="friend",
            )

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

            self._notify_session_watch(
                target_conversation_id=clean_cid,
                target_turn_id=created.turn_id,
                origin="friend",
            )

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

    def _notify_session_watch(
        self,
        *,
        target_conversation_id: str,
        target_turn_id: str,
        origin: str = "friend",
    ) -> None:
        friend_cid = str(getattr(self, "_conversation_id", "") or "") or None
        callback = getattr(self, "_on_session_watch", None)
        if callable(callback):
            try:
                callback(
                    user_id=self._project_user_id,
                    friend_conversation_id=friend_cid,
                    target_conversation_id=target_conversation_id,
                    target_turn_id=target_turn_id,
                    origin=str(origin or "friend"),
                )
            except Exception:
                pass
            return

        engine = getattr(getattr(self, "_chat_store", None), "_engine", None)
        if engine is not None and self._project_user_id:
            try:
                discord_store = getattr(self, "_cached_discord_store", None)
                if discord_store is None or getattr(discord_store, "_engine", None) is not engine:
                    from agentos.discord.store import PostgresDiscordStore

                    discord_store = PostgresDiscordStore(engine)
                    setattr(self, "_cached_discord_store", discord_store)
                cfg = discord_store.get_config(self._project_user_id)
                notify_channel = cfg.default_notify_channel_id if cfg is not None else None
                discord_store.upsert_watch(
                    self._project_user_id,
                    target_conversation_id,
                    target_turn_id,
                    friend_conversation_id=friend_cid,
                    origin=str(origin or "friend"),
                    discord_channel_id=notify_channel,
                )
            except Exception:
                pass

    def _require_owned_session(self, conversation_id: str | None) -> tuple[str, dict[str, Any]]:
        if self._chat_store is None or not self._project_user_id:
            raise AgentToolError("Chat store is not available to manage sessions.")
        if not isinstance(conversation_id, str) or not (1 <= len(conversation_id.strip()) <= 120):
            raise AgentToolError("conversation_id must be a non-blank string.")
        clean_cid = conversation_id.strip()
        active_cid = str(getattr(self, "_conversation_id", "") or "").strip()
        if active_cid and clean_cid == active_cid:
            raise AgentToolError(
                f"Cannot control or inspect the current active session '{clean_cid}' recursively."
            )
        try:
            conv = self._chat_store.get(clean_cid, self._project_user_id)
        except Exception as error:
            raise AgentToolError(f"Session '{clean_cid}' not found or access denied.") from error
        if not isinstance(conv, dict) or (
            conv.get("user_id") is not None and conv.get("user_id") != self._project_user_id
        ):
            raise AgentToolError(f"Session '{clean_cid}' not found or access denied.")
        conv_mode = str(conv.get("mode") or "chat").strip().lower()
        if conv_mode == "friend":
            raise AgentToolError(
                f"Conversation '{clean_cid}' is an Orin Friend session and cannot be controlled via manage_session."
            )
        return clean_cid, conv

    def _resolve_project_reference(self, project_ref: str | None) -> tuple[str | None, Any | None]:
        clean_ref = str(project_ref or "").strip() if project_ref is not None else ""
        if not clean_ref:
            fallback_pid = str(self._project_id or "").strip() or None
            if fallback_pid and self._project_store is not None and self._project_user_id:
                try:
                    proj = self._project_store.get(fallback_pid, self._project_user_id)
                    if proj is not None:
                        return str(proj.project_id), proj
                except Exception:
                    pass
            return fallback_pid, None

        if self._project_store is None or not self._project_user_id:
            return clean_ref, None

        try:
            proj = self._project_store.get(clean_ref, self._project_user_id)
            if proj is not None:
                return str(proj.project_id), proj
        except Exception:
            proj = None

        if hasattr(self._project_store, "sidebar"):
            try:
                items = list(self._project_store.sidebar(self._project_user_id) or ())
                matched = next(
                    (
                        item
                        for item in items
                        if str(getattr(item, "project_id", "")).strip() == clean_ref
                        or str(getattr(item, "name", "")).strip().lower() == clean_ref.lower()
                    ),
                    None,
                )
                if matched is not None:
                    matched_pid = str(matched.project_id)
                    try:
                        full_proj = self._project_store.get(matched_pid, self._project_user_id)
                    except Exception:
                        full_proj = None
                    return matched_pid, (full_proj or matched)
            except Exception:
                pass

        raise AgentToolError(f"Project '{clean_ref}' not found.")

    def _resolve_conversation_workspace_root(self, clean_cid: str, conv: Mapping[str, Any]) -> Path | None:
        candidates: list[str] = []
        proj_id = str(conv.get("project_id") or "").strip()
        if proj_id and self._project_store is not None and self._local_workspaces is not None and self._project_user_id:
            try:
                proj = self._project_store.get(proj_id, self._project_user_id)
                if proj is not None:
                    ws_id = getattr(proj, "workspace_id", "") or f"workspace:{proj_id}"
                    root = self._local_workspaces.root_for(ws_id, self._project_user_id)
                    if root:
                        candidates.append(str(root))
            except Exception:
                pass
        if self._local_workspaces is not None and self._project_user_id:
            for ws_key in (f"workspace:{clean_cid}", clean_cid):
                try:
                    root = self._local_workspaces.root_for(ws_key, self._project_user_id)
                    if root:
                        candidates.append(str(root))
                except Exception:
                    pass
        try:
            from agentos.installation import orin_paths

            if self._project_user_id:
                managed_dir = orin_paths().user_workspaces(self._project_user_id) / clean_cid
                candidates.append(str(managed_dir))
        except Exception:
            pass
        if hasattr(self, "workspace") and getattr(self.workspace, "root", None) is not None:
            candidates.append(str(self.workspace.root))
        for raw_path in candidates:
            p = Path(raw_path)
            if p.exists() and p.is_dir():
                return p
        return Path(candidates[0]) if candidates else None

    def _find_pending_permission_requests(
        self,
        clean_cid: str,
        conv: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        roots_to_check: list[Path] = []
        ws_root = self._resolve_conversation_workspace_root(clean_cid, conv)
        if ws_root is not None:
            roots_to_check.append(ws_root)
        if hasattr(self, "workspace") and getattr(self.workspace, "root", None) is not None:
            own_root = Path(self.workspace.root)
            if own_root not in roots_to_check:
                roots_to_check.append(own_root)

        for root in roots_to_check:
            perm_dirs: list[Path] = []
            try:
                from agentos.code_mode.permissions import ToolPermissionBroker

                perm_dirs.append(ToolPermissionBroker(root).permissions_dir)
            except Exception:
                pass
            perm_dirs.append(root / ".orin" / "permissions")
            for perm_dir in perm_dirs:
                if not perm_dir.is_dir():
                    continue
                for json_file in sorted(perm_dir.glob("*.json")):
                    try:
                        data = json.loads(json_file.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if not isinstance(data, dict) or data.get("status") != "pending":
                        continue
                    req_cid = str(data.get("conversation_id") or "").strip()
                    if req_cid and req_cid != clean_cid:
                        continue
                    req_id = str(data.get("request_id") or json_file.stem).strip()
                    if req_id:
                        found[req_id] = {**data, "_workspace_root": str(root)}

        for act in reversed(list(conv.get("activities") or ())):
            if not isinstance(act, Mapping):
                continue
            payload = act.get("payload")
            if not isinstance(payload, Mapping):
                continue
            perm_req = payload.get("permission_request")
            if isinstance(perm_req, Mapping) and perm_req.get("status") == "pending":
                req_id = str(perm_req.get("request_id") or payload.get("permission_request_id") or "").strip()
                if req_id and req_id not in found:
                    found[req_id] = dict(perm_req)
        return list(found.values())

    def _extract_session_inspection(
        self,
        clean_cid: str,
        conv: Mapping[str, Any],
        *,
        message_limit: int = 10,
    ) -> dict[str, Any]:
        turns = list(conv.get("turns") or ())
        last_turn = dict(turns[-1]) if turns and isinstance(turns[-1], Mapping) else None
        activities = list(conv.get("activities") or ())
        if not activities and getattr(self._chat_store, "_engine", None) is not None:
            try:
                from sqlalchemy import select
                from agentos.persistence.postgres.schema import conversation_activity_events

                with self._chat_store._engine.connect() as conn:
                    rows = (
                        conn.execute(
                            select(conversation_activity_events)
                            .where(
                                conversation_activity_events.c.conversation_id == clean_cid,
                                conversation_activity_events.c.user_id == self._project_user_id,
                            )
                            .order_by(conversation_activity_events.c.id.desc())
                            .limit(400)
                        )
                        .mappings()
                        .all()
                    )
                    activities = [dict(r) for r in reversed(rows)]
            except Exception:
                pass

        todos: list[dict[str, Any]] = []
        files_modified: list[str] = []
        recent_events: list[dict[str, Any]] = []
        latest_questions: list[dict[str, Any]] | None = None
        open_in_turn_questions: list[dict[str, Any]] | None = None
        in_turn_waiting = False
        in_turn_answered = False
        latest_plan_approval: dict[str, Any] | None = None

        last_turn_id = str(last_turn.get("turn_id") or "").strip() if last_turn else ""
        for act in activities:
            if not isinstance(act, Mapping):
                continue
            evt_type = str(act.get("event_type") or "")
            summary = str(act.get("summary") or "")
            raw_payload = act.get("payload")
            if isinstance(raw_payload, str):
                try:
                    raw_payload = json.loads(raw_payload)
                except Exception:
                    raw_payload = {}
            payload = raw_payload if isinstance(raw_payload, Mapping) else {}
            tool_name = str(payload.get("tool_name") or payload.get("name") or "")
            is_subagent = bool(act.get("parent_agent_id") or payload.get("parent_agent_id"))

            raw_todos = payload.get("todos")
            if isinstance(raw_todos, list):
                todos = [dict(t) for t in raw_todos if isinstance(t, Mapping)]
            else:
                tool_out = payload.get("tool_output")
                if isinstance(tool_out, Mapping) and isinstance(tool_out.get("todos"), list):
                    todos = [dict(t) for t in tool_out["todos"] if isinstance(t, Mapping)]

            for path_key in ("path", "file_path", "filePath"):
                p_val = payload.get(path_key)
                if not p_val and isinstance(payload.get("tool_output"), Mapping):
                    p_val = payload["tool_output"].get(path_key)
                if isinstance(p_val, str) and p_val.strip():
                    if tool_name in {"write_file", "edit_file", "Write", "Edit", "notebook_edit", "NotebookEdit"} or payload.get("tool_kind") in {"write", "edit"} or "structured_patch" in payload:
                        if p_val.strip() not in files_modified:
                            files_modified.append(p_val.strip())

            act_turn_id = str(act.get("turn_id") or "").strip()
            matches_last_turn = not last_turn_id or not act_turn_id or act_turn_id == last_turn_id

            if matches_last_turn and not is_subagent:
                payload_state = str(payload.get("state") or "").strip().lower()
                has_questions = isinstance(payload.get("questions"), list) and bool(payload.get("questions"))
                if has_questions:
                    qs = [dict(q) for q in payload["questions"] if isinstance(q, Mapping)]
                    if qs:
                        latest_questions = qs
                        if (
                            bool(payload.get("wait_for_user"))
                            or tool_name in {"ask_user", "AskUserQuestion"}
                            or str(payload.get("tool_kind") or "") == "user_input"
                        ):
                            open_in_turn_questions = qs
                            in_turn_answered = False

                if evt_type == "turn.waiting_user" and (
                    bool(payload.get("in_turn")) or payload_state == "waiting_user_in_turn"
                ):
                    if not in_turn_answered:
                        in_turn_waiting = True
                        if open_in_turn_questions is None and latest_questions:
                            open_in_turn_questions = latest_questions
                elif payload_state == "queued_input" or evt_type == "turn.queued_input":
                    in_turn_answered = True
                    in_turn_waiting = False
                    open_in_turn_questions = None
                elif evt_type in {"turn.completed", "turn.failed", "turn.cancelled"} or (
                    in_turn_waiting
                    and not has_questions
                    and (
                        evt_type == "tool.started"
                        or (evt_type == "tool.finished" and tool_name not in {"ask_user", "AskUserQuestion"})
                        or payload_state in {"running", "completed", "failed", "cancelled"}
                    )
                ):
                    in_turn_waiting = False
                    open_in_turn_questions = None

                if payload.get("plan_approval") or payload.get("code_approval") or payload.get("plan_ready"):
                    latest_plan_approval = {
                    "plan_path": payload.get("plan_path"),
                    "plan_summary": payload.get("plan_summary"),
                    "plan_content": payload.get("plan_content"),
                    "questions": payload.get("questions"),
                }

            if evt_type and evt_type not in {"agent.thought.delta", "context.updated"}:
                raw_created = act.get("created_at")
                recent_events.append(
                    {
                        "event_type": evt_type,
                        "summary": summary[:240],
                        "tool_name": tool_name or None,
                        "turn_id": act.get("turn_id"),
                        "created_at": str(raw_created) if raw_created is not None else None,
                    }
                )

        registry = getattr(self, "_milestone_registry", None) or get_milestone_watcher_registry()
        watcher = registry.get(clean_cid, user_id=self._project_user_id) if self._project_user_id else None
        if watcher is not None:
            snap = watcher.snapshot_status()
            for mod_file in snap.get("files_modified") or ():
                if mod_file and mod_file not in files_modified:
                    files_modified.append(str(mod_file))
            if not todos and getattr(watcher, "_todos", None):
                todos = [dict(t) for t in watcher._todos if isinstance(t, Mapping)]

        pending_permissions = self._find_pending_permission_requests(clean_cid, conv)
        clean_permissions = [
            {k: v for k, v in item.items() if k != "_workspace_root"}
            for item in pending_permissions
        ]

        conv_state = str(conv.get("state") or (last_turn.get("state") if last_turn else "idle") or "idle")
        turn_state = str(last_turn.get("state") or conv_state) if last_turn else conv_state
        is_turn_live = turn_state in {"running", "starting", "queued"}
        is_waiting = conv_state == "waiting_user" or turn_state == "waiting_user"
        is_in_turn_ask = is_turn_live and (in_turn_waiting or open_in_turn_questions is not None)

        pending_block: dict[str, Any] | None = None
        if clean_permissions:
            pending_block = {
                "kind": "tool_permission",
                "request": clean_permissions[-1],
                "all_requests": clean_permissions,
            }
        elif is_waiting and latest_plan_approval is not None:
            pending_block = {
                "kind": "plan_approval",
                **latest_plan_approval,
            }
        elif is_in_turn_ask and (open_in_turn_questions or latest_questions):
            pending_block = {
                "kind": "ask_user",
                "questions": open_in_turn_questions or latest_questions,
                "in_turn": True,
            }
        elif is_waiting and latest_questions:
            pending_block = {
                "kind": "ask_user",
                "questions": latest_questions,
            }
        elif is_waiting and str(conv.get("permission_mode") or "") == "plan_mode":
            pending_block = {
                "kind": "plan_approval",
                "plan_path": latest_plan_approval.get("plan_path") if latest_plan_approval else None,
                "plan_summary": latest_plan_approval.get("plan_summary") if latest_plan_approval else None,
            }
        elif watcher is not None and watcher.pending_question:
            pending_block = {
                "kind": "ask_user",
                "question": watcher.pending_question,
            }

        raw_messages = list(conv.get("messages") or ())
        tail_messages = [
            {
                "message_id": m.get("message_id"),
                "role": m.get("role"),
                "content": str(m.get("content") or "")[:2000],
                "status": m.get("status"),
                "turn_id": m.get("turn_id"),
            }
            for m in raw_messages[-message_limit:]
            if isinstance(m, Mapping)
        ]

        ws_root = self._resolve_conversation_workspace_root(clean_cid, conv)
        conv_mode = str(conv.get("mode") or "chat").strip().lower()
        perm_mode = str(conv.get("permission_mode") or ("accept_edits" if conv_mode == "code" else "ask_permissions"))

        return {
            "conversation_id": clean_cid,
            "title": conv.get("title"),
            "mode": conv_mode,
            "state": "waiting_user" if pending_block is not None and conv_state == "running" else conv_state,
            "project_id": conv.get("project_id"),
            "project_name": conv.get("project_name"),
            "workspace_path": str(ws_root) if ws_root is not None else None,
            "provider": conv.get("provider"),
            "model_id": conv.get("model_id"),
            "permission_mode": perm_mode,
            "public_permission_mode": _public_permission_mode(perm_mode),
            "current_turn": last_turn,
            "todos": todos,
            "todos_completed": sum(1 for t in todos if str(t.get("status") or "") == "completed"),
            "todos_total": len(todos),
            "files_modified": files_modified,
            "recent_activities": recent_events[-15:],
            "pending_block": pending_block,
            "pending_question": latest_questions if (is_waiting and latest_questions) else (watcher.pending_question if watcher else None),
            "pending_plan_approval": latest_plan_approval if is_waiting else None,
            "pending_permission_requests": clean_permissions,
            "messages": tail_messages,
        }

    def manage_session(
        self,
        action: str = "list",
        conversation_id: str | None = None,
        code_conversation_id: str | None = None,
        mode: str | None = None,
        project_id: str | None = None,
        workspace_path: str | None = None,
        title: str = "",
        prompt: str = "",
        message: str | None = None,
        provider: str | None = None,
        model_id: str | None = None,
        permission_mode: str | None = None,
        state: str | None = None,
        decision: str | None = None,
        feedback: str | None = None,
        request_id: str | None = None,
        answers: Any = None,
        limit: int = 20,
        message_limit: int = 10,
        origin: str = "friend",
    ) -> ToolOutcome:
        if self._chat_store is None or not self._project_user_id:
            raise AgentToolError("Chat store is not available to manage sessions.")

        effective_action = str(action or "list").strip().lower()
        if effective_action not in _ALLOWED_MANAGE_SESSION_ACTIONS:
            raise AgentToolError(
                f"action must be one of {sorted(_ALLOWED_MANAGE_SESSION_ACTIONS)}."
            )

        target_cid = conversation_id if conversation_id is not None else code_conversation_id

        if effective_action == "list":
            clean_mode_filter = str(mode or "").strip().lower() if mode is not None else ""
            if clean_mode_filter == "friend":
                raise AgentToolError("mode filter must be 'chat' or 'code'.")
            if clean_mode_filter and clean_mode_filter not in {"chat", "code"}:
                raise AgentToolError("mode filter must be 'chat' or 'code'.")

            clean_state_filter = str(state or "").strip().lower() if state is not None else ""
            resolved_pid_filter: str | None = None
            if project_id is not None and str(project_id).strip():
                resolved_pid_filter, _ = self._resolve_project_reference(project_id)

            if isinstance(limit, bool):
                raise AgentToolError("limit must be an integer between 1 and 100.")
            try:
                effective_limit = min(max(int(limit), 1), 100)
            except (TypeError, ValueError) as error:
                raise AgentToolError("limit must be an integer between 1 and 100.") from error

            active_self_cid = str(getattr(self, "_conversation_id", "") or "").strip()
            sessions_list: list[dict[str, Any]] = []
            engine = getattr(self._chat_store, "_engine", None)
            if engine is not None:
                from sqlalchemy import select
                from agentos.persistence.postgres.schema import conversations

                stmt = (
                    select(conversations)
                    .where(
                        conversations.c.user_id == self._project_user_id,
                        conversations.c.mode != "friend",
                    )
                    .order_by(conversations.c.updated_at.desc())
                )
                if resolved_pid_filter is not None:
                    stmt = stmt.where(conversations.c.project_id == resolved_pid_filter)
                if clean_mode_filter:
                    stmt = stmt.where(conversations.c.mode == clean_mode_filter)
                if clean_state_filter:
                    stmt = stmt.where(conversations.c.state == clean_state_filter)
                stmt = stmt.limit(effective_limit + 5)
                with engine.connect() as conn:
                    rows = conn.execute(stmt).mappings().all()
                for row in rows:
                    cid = str(row["conversation_id"])
                    if active_self_cid and cid == active_self_cid:
                        continue
                    row_mode = str(row.get("mode") or "chat").strip().lower()
                    if row_mode == "friend":
                        continue
                    updated_val = row.get("updated_at")
                    updated_iso = updated_val.isoformat() if hasattr(updated_val, "isoformat") else str(updated_val or "")
                    sessions_list.append(
                        {
                            "conversation_id": cid,
                            "title": row.get("title"),
                            "mode": row_mode,
                            "project_id": str(row["project_id"]) if row.get("project_id") else None,
                            "provider": row.get("provider"),
                            "model_id": row.get("model_id"),
                            "permission_mode": str(
                                row.get("permission_mode")
                                or ("accept_edits" if row_mode == "code" else "ask_permissions")
                            ),
                            "state": str(row.get("state") or "idle"),
                            "updated_at": updated_iso,
                        }
                    )
                    if len(sessions_list) >= effective_limit:
                        break
            else:
                raw_items: list[dict[str, Any]] = []
                if hasattr(self._chat_store, "list"):
                    listed = self._chat_store.list(self._project_user_id)
                    if isinstance(listed, Mapping):
                        raw_items.extend(dict(x) for x in (listed.get("items") or ()) if isinstance(x, Mapping))
                for item in raw_items:
                    cid = str(item.get("conversation_id") or "")
                    if not cid or (active_self_cid and cid == active_self_cid):
                        continue
                    full_conv = item
                    if hasattr(self._chat_store, "get"):
                        try:
                            full_conv = self._chat_store.get(cid, self._project_user_id)
                        except Exception:
                            full_conv = item
                    row_mode = str(full_conv.get("mode") or item.get("mode") or "chat").strip().lower()
                    if row_mode == "friend":
                        continue
                    row_pid = str(full_conv.get("project_id") or "") or None
                    row_state = str(full_conv.get("state") or item.get("state") or "idle")
                    if resolved_pid_filter is not None and row_pid != resolved_pid_filter:
                        continue
                    if clean_mode_filter and row_mode != clean_mode_filter:
                        continue
                    if clean_state_filter and row_state.lower() != clean_state_filter:
                        continue
                    sessions_list.append(
                        {
                            "conversation_id": cid,
                            "title": full_conv.get("title") or item.get("title"),
                            "mode": row_mode,
                            "project_id": row_pid,
                            "provider": full_conv.get("provider"),
                            "model_id": full_conv.get("model_id"),
                            "permission_mode": str(
                                full_conv.get("permission_mode")
                                or ("accept_edits" if row_mode == "code" else "ask_permissions")
                            ),
                            "state": row_state,
                            "updated_at": item.get("updated_at") or full_conv.get("updated_at"),
                        }
                    )
                    if len(sessions_list) >= effective_limit:
                        break

            payload = {
                "action": "list",
                "count": len(sessions_list),
                "sessions": sessions_list,
                "project_id": resolved_pid_filter,
                "mode": clean_mode_filter or None,
                "state": clean_state_filter or None,
                "wait_for_user": False,
                "tool_kind": "session_management",
            }
            return ToolOutcome(
                "succeeded",
                f"{len(sessions_list)} sessão(ões) encontrada(s)",
                json.dumps(payload, ensure_ascii=False),
                payload,
            )

        if effective_action == "inspect":
            clean_cid, conv = self._require_owned_session(target_cid)
            if isinstance(message_limit, bool):
                raise AgentToolError("message_limit must be an integer between 1 and 50.")
            try:
                eff_msg_limit = min(max(int(message_limit), 1), 50)
            except (TypeError, ValueError) as error:
                raise AgentToolError("message_limit must be an integer between 1 and 50.") from error

            inspection = self._extract_session_inspection(clean_cid, conv, message_limit=eff_msg_limit)
            payload = {
                "action": "inspect",
                **inspection,
                "code_conversation_id": clean_cid,
                "wait_for_user": False,
                "tool_kind": "session_management",
            }
            return ToolOutcome(
                "succeeded",
                f"Inspeção da sessão {clean_cid} ({inspection['mode']} · {inspection['state']})",
                json.dumps(payload, ensure_ascii=False, default=str),
                payload,
            )

        if effective_action == "start":
            clean_target_mode = str(mode or "code").strip().lower()
            if clean_target_mode == "friend":
                raise AgentToolError("mode must be 'chat' or 'code'. Controlling 'friend' sessions is not allowed.")
            if clean_target_mode not in {"chat", "code"}:
                raise AgentToolError("mode must be 'chat' or 'code'.")

            raw_prompt = prompt if (isinstance(prompt, str) and prompt.strip()) else (message or "")
            if not isinstance(raw_prompt, str) or not (1 <= len(raw_prompt.strip()) <= 16000):
                raise AgentToolError("prompt must be a non-blank string between 1 and 16000 characters.")
            clean_prompt = raw_prompt.strip()

            if not isinstance(title, str):
                raise AgentToolError("title must be a string.")
            clean_title = title.strip() or clean_prompt.splitlines()[0][:80].strip()
            if not (1 <= len(clean_title) <= 200):
                raise AgentToolError("title must be between 1 and 200 characters.")

            resolved_pid, project_record = self._resolve_project_reference(project_id)
            explicit_ws = workspace_path.strip() if isinstance(workspace_path, str) and workspace_path.strip() else None
            project_ws_path: str | None = None
            if project_record is not None and self._local_workspaces is not None:
                proj_ws_id = getattr(project_record, "workspace_id", None) or f"workspace:{resolved_pid}"
                try:
                    project_ws_path = self._local_workspaces.root_for(proj_ws_id, self._project_user_id)
                except Exception:
                    project_ws_path = None

            default_perm = "accept_edits" if clean_target_mode == "code" else "ask_permissions"
            effective_path, effective_provider, effective_model, clean_perm = self._resolve_session_defaults(
                workspace_path=explicit_ws or project_ws_path,
                provider=provider,
                model_id=model_id,
                permission_mode=permission_mode,
                allow_full_autonomy=True,
                default_permission_mode=default_perm,
            )

            try:
                created = self._chat_store.create(
                    user_id=self._project_user_id,
                    message=clean_prompt,
                    provider=effective_provider,
                    model_id=effective_model,
                    idempotency_key=f"sess_start_{uuid4().hex[:16]}",
                    project_id=resolved_pid,
                    code_mode=clean_target_mode,
                    permission_mode=clean_perm,
                )
            except Exception as error:
                raise AgentToolError(f"Failed to start {clean_target_mode} session: {error}") from error

            if clean_title and callable(getattr(self._chat_store, "rename", None)):
                try:
                    self._chat_store.rename(created.conversation_id, self._project_user_id, clean_title)
                except Exception:
                    pass

            if effective_path and self._local_workspaces is not None:
                if project_record is not None and (explicit_ws or not project_ws_path):
                    proj_ws_id = getattr(project_record, "workspace_id", None) or f"workspace:{resolved_pid}"
                    try:
                        self._local_workspaces.set_root(proj_ws_id, self._project_user_id, effective_path)
                    except Exception:
                        pass
                for ws_key in (f"workspace:{created.conversation_id}", created.conversation_id):
                    try:
                        self._local_workspaces.set_root(ws_key, self._project_user_id, effective_path)
                    except Exception:
                        pass

            if clean_target_mode == "code":
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

            self._notify_session_watch(
                target_conversation_id=created.conversation_id,
                target_turn_id=created.turn_id,
                origin=origin,
            )

            dispatcher = getattr(self, "_code_turn_dispatcher", None)
            if callable(dispatcher):
                dispatcher(created.conversation_id, created.turn_id)

            payload = {
                "action": "start",
                "conversation_id": created.conversation_id,
                "code_conversation_id": created.conversation_id,
                "turn_id": created.turn_id,
                "mode": clean_target_mode,
                "state": created.state,
                "title": clean_title,
                "project_id": resolved_pid,
                "workspace_path": effective_path,
                "provider": effective_provider,
                "model_id": effective_model,
                "permission_mode": clean_perm,
                "public_permission_mode": _public_permission_mode(clean_perm),
                "wait_for_user": False,
                "tool_kind": "session_management",
            }
            return ToolOutcome(
                "succeeded",
                f"Sessão {clean_target_mode} iniciada: {clean_title}",
                json.dumps(payload, ensure_ascii=False),
                payload,
            )

        if effective_action == "send_message":
            clean_cid, conv = self._require_owned_session(target_cid)
            raw_msg = message if message is not None else prompt
            if not isinstance(raw_msg, str) or not (1 <= len(raw_msg.strip()) <= 16000):
                raise AgentToolError("message/prompt must be a non-blank string between 1 and 16000 characters.")
            clean_msg = raw_msg.strip()
            conv_mode = str(conv.get("mode") or "chat").strip().lower()
            default_perm = str(
                conv.get("permission_mode") or ("accept_edits" if conv_mode == "code" else "ask_permissions")
            )

            if provider is not None or model_id is not None or permission_mode is not None:
                _, resolved_prov, resolved_mod, effective_perm = self._resolve_session_defaults(
                    workspace_path=None,
                    provider=provider if provider is not None else str(conv.get("provider") or ""),
                    model_id=model_id if model_id is not None else (None if provider is not None else str(conv.get("model_id") or "")),
                    permission_mode=permission_mode or default_perm,
                    allow_full_autonomy=True,
                    default_permission_mode=default_perm,
                )
                effective_provider = resolved_prov or conv.get("provider") or self._default_provider
                effective_model = resolved_mod or conv.get("model_id") or self._default_model_id
                if permission_mode is not None and effective_perm != default_perm and callable(getattr(self._chat_store, "set_permission_mode", None)):
                    try:
                        self._chat_store.set_permission_mode(clean_cid, self._project_user_id, effective_perm)
                    except Exception:
                        pass
            else:
                effective_provider = conv.get("provider") or self._default_provider
                effective_model = conv.get("model_id") or self._default_model_id
                effective_perm = default_perm

            try:
                created = self._chat_store.create(
                    user_id=self._project_user_id,
                    message=clean_msg,
                    provider=effective_provider,
                    model_id=effective_model,
                    idempotency_key=f"sess_msg_{uuid4().hex[:16]}",
                    conversation_id=clean_cid,
                    project_id=conv.get("project_id") or self._project_id,
                    code_mode=conv_mode,
                    permission_mode=effective_perm,
                )
            except Exception as error:
                raise AgentToolError(f"Failed to send message to session '{clean_cid}': {error}") from error

            registry = getattr(self, "_milestone_registry", None) or get_milestone_watcher_registry()
            watcher = registry.get(clean_cid, user_id=self._project_user_id)
            if watcher is not None:
                watcher.pending_question = None

            self._notify_session_watch(
                target_conversation_id=clean_cid,
                target_turn_id=created.turn_id,
                origin=origin,
            )

            dispatcher = getattr(self, "_code_turn_dispatcher", None)
            if callable(dispatcher):
                dispatcher(clean_cid, created.turn_id)

            payload = {
                "action": "send_message",
                "conversation_id": clean_cid,
                "code_conversation_id": clean_cid,
                "turn_id": created.turn_id,
                "mode": conv_mode,
                "state": created.state,
                "provider": effective_provider,
                "model_id": effective_model,
                "permission_mode": effective_perm,
                "wait_for_user": False,
                "tool_kind": "session_management",
            }
            return ToolOutcome(
                "succeeded",
                f"Mensagem enviada para a sessão {clean_cid}",
                json.dumps(payload, ensure_ascii=False),
                payload,
            )

        if effective_action == "respond":
            clean_cid, conv = self._require_owned_session(target_cid)
            conv_mode = str(conv.get("mode") or "chat").strip().lower()
            clean_decision = str(decision or "").strip().lower() if decision is not None else ""

            # 1) ToolPermissionBroker decision: allow_once | allow_always | deny
            if clean_decision in _ALLOWED_PERMISSION_DECISIONS or (request_id and not clean_decision):
                eff_perm_decision = clean_decision or "allow_once"
                if eff_perm_decision not in _ALLOWED_PERMISSION_DECISIONS:
                    raise AgentToolError(
                        f"Tool permission decision must be one of {sorted(_ALLOWED_PERMISSION_DECISIONS)}."
                    )
                from agentos.code_mode.permissions import ToolPermissionBroker

                pending_reqs = self._find_pending_permission_requests(clean_cid, conv)
                target_req_id = str(request_id or "").strip()
                matched_req = None
                if target_req_id:
                    matched_req = next((r for r in pending_reqs if r.get("request_id") == target_req_id), None)
                elif pending_reqs:
                    matched_req = pending_reqs[-1]
                    target_req_id = str(matched_req.get("request_id") or "")

                if not target_req_id:
                    raise AgentToolError(
                        f"No pending tool permission request found for session '{clean_cid}'."
                    )
                ws_root_str = (
                    matched_req.get("_workspace_root")
                    if isinstance(matched_req, Mapping) and matched_req.get("_workspace_root")
                    else None
                )
                ws_root = (
                    Path(str(ws_root_str))
                    if ws_root_str
                    else (self._resolve_conversation_workspace_root(clean_cid, conv) or self.workspace.root)
                )
                broker = ToolPermissionBroker(ws_root)
                try:
                    resolved = broker.resolve_request(
                        target_req_id,
                        decision=eff_perm_decision,  # type: ignore[arg-type]
                        reason=str(feedback or message or "").strip() or None,
                    )
                except Exception as error:
                    raise AgentToolError(f"Failed to resolve permission request '{target_req_id}': {error}") from error

                payload = {
                    "action": "respond",
                    "response_kind": "tool_permission",
                    "conversation_id": clean_cid,
                    "code_conversation_id": clean_cid,
                    "request_id": target_req_id,
                    "decision": eff_perm_decision,
                    "allowed": bool(resolved.get("allowed")),
                    "permission_record": resolved,
                    "wait_for_user": False,
                    "tool_kind": "session_management",
                }
                return ToolOutcome(
                    "succeeded",
                    f"Permissão '{eff_perm_decision}' aplicada na sessão {clean_cid} ({target_req_id})",
                    json.dumps(payload, ensure_ascii=False),
                    payload,
                )

            # 2) Plan approval decision: approve | reject | revise
            if clean_decision in _ALLOWED_DECISIONS:
                raw_feedback = feedback if feedback is not None else (message or prompt or "")
                if not isinstance(raw_feedback, str) or len(raw_feedback) > 8000:
                    raise AgentToolError("feedback must be a string up to 8000 characters.")
                clean_feedback = raw_feedback.strip()

                effective_perm = str(
                    conv.get("permission_mode") or ("accept_edits" if conv_mode == "code" else "ask_permissions")
                )
                if clean_decision == "approve" and effective_perm == "plan_mode":
                    if callable(getattr(self._chat_store, "set_permission_mode", None)):
                        try:
                            updated = self._chat_store.set_permission_mode(
                                clean_cid,
                                self._project_user_id,
                                "accept_edits",
                            )
                            effective_perm = str(updated.get("permission_mode") or "accept_edits")
                        except Exception as error:
                            raise AgentToolError(f"Failed to update permission mode on approval: {error}") from error
                    else:
                        effective_perm = "accept_edits"

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
                        idempotency_key=f"sess_plan_{uuid4().hex[:16]}",
                        conversation_id=clean_cid,
                        project_id=conv.get("project_id") or self._project_id,
                        code_mode=conv_mode,
                        permission_mode=effective_perm,
                    )
                except Exception as error:
                    raise AgentToolError(f"Failed to submit plan decision to session '{clean_cid}': {error}") from error

                registry = getattr(self, "_milestone_registry", None) or get_milestone_watcher_registry()
                watcher = registry.get(clean_cid, user_id=self._project_user_id)
                if watcher is not None:
                    watcher.pending_question = None

                self._notify_session_watch(
                    target_conversation_id=clean_cid,
                    target_turn_id=created.turn_id,
                    origin=origin,
                )

                dispatcher = getattr(self, "_code_turn_dispatcher", None)
                if callable(dispatcher):
                    dispatcher(clean_cid, created.turn_id)

                payload = {
                    "action": "respond",
                    "response_kind": "plan_decision",
                    "conversation_id": clean_cid,
                    "code_conversation_id": clean_cid,
                    "decision": clean_decision,
                    "permission_mode": effective_perm,
                    "turn_id": created.turn_id,
                    "state": created.state,
                    "wait_for_user": False,
                    "tool_kind": "session_management",
                }
                return ToolOutcome(
                    "succeeded",
                    f"Decisão '{clean_decision}' enviada à sessão {clean_cid}",
                    json.dumps(payload, ensure_ascii=False),
                    payload,
                )

            if clean_decision:
                raise AgentToolError(
                    f"Invalid decision '{clean_decision}'. Expected one of {sorted(_ALLOWED_DECISIONS | _ALLOWED_PERMISSION_DECISIONS)}."
                )

            # 3) Answer to AskUserQuestion or paused prompt
            if answers is not None:
                if isinstance(answers, str):
                    reply_text = answers.strip()
                elif isinstance(answers, Mapping):
                    parts = [f"{k}: {v}" for k, v in answers.items()]
                    reply_text = "\n".join(parts).strip()
                else:
                    reply_text = json.dumps(answers, ensure_ascii=False)
            else:
                raw_ans = message if message is not None else (feedback if feedback is not None else prompt)
                reply_text = str(raw_ans or "").strip()

            if not reply_text or len(reply_text) > 16000:
                raise AgentToolError(
                    "respond requires either 'decision' ('approve'|'reject'|'revise'|'allow_once'|'allow_always'|'deny') "
                    "or a non-blank 'message'/'answers'/'feedback' up to 16000 characters."
                )

            effective_perm = str(
                conv.get("permission_mode") or ("accept_edits" if conv_mode == "code" else "ask_permissions")
            )
            turns_list = [t for t in (conv.get("turns") or ()) if isinstance(t, Mapping)]
            active_turn = turns_list[-1] if turns_list else None
            active_turn_state = str(
                (active_turn.get("state") if active_turn else conv.get("state")) or ""
            ).strip().lower()
            is_live = active_turn_state in {"running", "starting", "queued"}

            if is_live and hasattr(self._chat_store, "enqueue_input"):
                ans_header = "Respostas às perguntas do agente:"
                if reply_text.lstrip().startswith(ans_header):
                    live_reply = reply_text
                else:
                    q_label = "Pergunta"
                    try:
                        insp = self._extract_session_inspection(clean_cid, conv, message_limit=2)
                        pb = insp.get("pending_block") if isinstance(insp, Mapping) else None
                        if isinstance(pb, Mapping) and isinstance(pb.get("questions"), list) and pb["questions"]:
                            first_q = pb["questions"][0]
                            if isinstance(first_q, Mapping) and first_q.get("question"):
                                q_label = str(first_q["question"]).strip()
                    except Exception:
                        pass
                    live_reply = f"{ans_header}\n\n{q_label}\nSeleção: {reply_text}"

                try:
                    queued_item = self._chat_store.enqueue_input(
                        user_id=self._project_user_id,
                        conversation_id=clean_cid,
                        content=live_reply,
                        idempotency_key=f"sess_ans_{uuid4().hex[:16]}",
                    )
                except Exception as error:
                    raise AgentToolError(f"Failed to enqueue answer to live session '{clean_cid}': {error}") from error

                registry = getattr(self, "_milestone_registry", None) or get_milestone_watcher_registry()
                watcher = registry.get(clean_cid, user_id=self._project_user_id)
                if watcher is not None:
                    watcher.pending_question = None

                live_turn_id = str((active_turn or {}).get("turn_id") or "")
                if live_turn_id:
                    self._notify_session_watch(
                        target_conversation_id=clean_cid,
                        target_turn_id=live_turn_id,
                        origin=origin,
                    )
                payload = {
                    "action": "respond",
                    "response_kind": "ask_user",
                    "queued_in_turn": True,
                    "queue_id": queued_item.get("queue_id") if isinstance(queued_item, Mapping) else None,
                    "conversation_id": clean_cid,
                    "code_conversation_id": clean_cid,
                    "turn_id": live_turn_id,
                    "state": active_turn_state,
                    "wait_for_user": False,
                    "tool_kind": "session_management",
                }
                return ToolOutcome(
                    "succeeded",
                    f"Resposta enfileirada para o turno ativo da sessão {clean_cid}",
                    json.dumps(payload, ensure_ascii=False),
                    payload,
                )

            try:
                created = self._chat_store.create(
                    user_id=self._project_user_id,
                    message=reply_text,
                    provider=conv.get("provider") or self._default_provider,
                    model_id=conv.get("model_id") or self._default_model_id,
                    idempotency_key=f"sess_ans_{uuid4().hex[:16]}",
                    conversation_id=clean_cid,
                    project_id=conv.get("project_id") or self._project_id,
                    code_mode=conv_mode,
                    permission_mode=effective_perm,
                )
            except Exception as error:
                raise AgentToolError(f"Failed to send answer to session '{clean_cid}': {error}") from error

            registry = getattr(self, "_milestone_registry", None) or get_milestone_watcher_registry()
            watcher = registry.get(clean_cid, user_id=self._project_user_id)
            if watcher is not None:
                watcher.pending_question = None

            self._notify_session_watch(
                target_conversation_id=clean_cid,
                target_turn_id=created.turn_id,
                origin=origin,
            )

            dispatcher = getattr(self, "_code_turn_dispatcher", None)
            if callable(dispatcher):
                dispatcher(clean_cid, created.turn_id)

            payload = {
                "action": "respond",
                "response_kind": "ask_user",
                "conversation_id": clean_cid,
                "code_conversation_id": clean_cid,
                "turn_id": created.turn_id,
                "state": created.state,
                "wait_for_user": False,
                "tool_kind": "session_management",
            }
            return ToolOutcome(
                "succeeded",
                f"Resposta enviada para a sessão {clean_cid}",
                json.dumps(payload, ensure_ascii=False),
                payload,
            )

        # effective_action == "stop"
        clean_cid, conv = self._require_owned_session(target_cid)
        try:
            cancel_result = self._chat_store.request_cancel(clean_cid, self._project_user_id)
        except Exception as error:
            raise AgentToolError(f"Failed to stop session '{clean_cid}': {error}") from error

        payload = {
            "action": "stop",
            "conversation_id": clean_cid,
            "code_conversation_id": clean_cid,
            "mode": str(conv.get("mode") or "chat"),
            "state": cancel_result.get("state", "cancelled"),
            "cancelled_turn_ids": cancel_result.get("cancelled_turn_ids", []),
            "wait_for_user": False,
            "tool_kind": "session_management",
        }
        return ToolOutcome(
            "succeeded",
            f"Sessão interrompida ({clean_cid})",
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
        around_sequence: int | None = None,
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
        effective_around: int | None = None
        if around_sequence is not None:
            if isinstance(around_sequence, bool):
                raise AgentToolError("around_sequence must be a non-negative integer.")
            try:
                effective_around = int(around_sequence)
            except (TypeError, ValueError) as error:
                raise AgentToolError("around_sequence must be a non-negative integer.") from error
            if effective_around < 0:
                raise AgentToolError("around_sequence must be a non-negative integer.")
        try:
            call_kwargs: dict[str, Any] = {"limit": effective_limit}
            if effective_around is not None:
                call_kwargs["around_sequence"] = effective_around
            result = self._chat_store.read_project_conversation(
                clean_cid,
                self._project_user_id,
                **call_kwargs,
            )
        except Exception as error:
            raise AgentToolError(f"Conversation '{clean_cid}' not found or not accessible.") from error
        return {
            "summary": f"Leu conversa {result.get('title', clean_cid)}",
            "content": json.dumps(result, ensure_ascii=False),
            "payload": {
                "conversation_id": clean_cid,
                "title": result.get("title"),
                "around_sequence": effective_around,
                "tool_kind": "project",
                "project_action": "read_conversation",
            },
        }

    def search_conversations(
        self,
        query: str,
        scope: str | None = None,
        mode: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        if self._chat_store is None or not self._project_user_id:
            raise AgentToolError("Chat store is not available.")
        clean_query = str(query or "").strip() if isinstance(query, str) else ""
        if not clean_query:
            raise AgentToolError("query must be a non-blank string.")
        current_conversation_id = (
            str(self._conversation_id).strip()
            if getattr(self, "_conversation_id", None) and str(self._conversation_id).strip()
            else None
        )
        active_project_id = (
            str(self._project_id).strip()
            if getattr(self, "_project_id", None) and str(self._project_id).strip()
            else None
        )
        caller_is_friend = bool(getattr(self, "_friend_mode_active", False))
        if current_conversation_id and hasattr(self._chat_store, "get") and (not active_project_id or not caller_is_friend):
            try:
                current_conv = self._chat_store.get(current_conversation_id, self._project_user_id)
                if isinstance(current_conv, dict):
                    if not active_project_id and current_conv.get("project_id"):
                        active_project_id = str(current_conv["project_id"]).strip() or None
                    if str(current_conv.get("mode") or "").strip().lower() == "friend":
                        caller_is_friend = True
            except Exception:
                pass
        clean_scope = str(scope or "").strip().lower() if scope is not None else ""
        if clean_scope and clean_scope not in {"project", "all"}:
            raise AgentToolError("scope must be 'project' or 'all'.")
        effective_scope = clean_scope or ("project" if active_project_id else "all")
        clean_mode = str(mode or "").strip().lower() if mode is not None else None
        if clean_mode == "":
            clean_mode = None
        if clean_mode is not None and clean_mode not in {"chat", "code", "friend"}:
            raise AgentToolError("mode must be 'chat', 'code', or 'friend'.")
        if isinstance(limit, bool):
            raise AgentToolError("limit must be an integer between 1 and 25.")
        try:
            effective_limit = min(max(int(limit), 1), 25)
        except (TypeError, ValueError) as error:
            raise AgentToolError("limit must be an integer between 1 and 25.") from error
        try:
            search_kwargs: dict[str, Any] = {
                "query": clean_query,
                "project_id": active_project_id if effective_scope != "project" or active_project_id else None,
                "scope": effective_scope,
                "mode": clean_mode,
                "exclude_conversation_id": current_conversation_id,
                "limit": effective_limit,
            }
            if caller_is_friend:
                search_kwargs["include_friend"] = True
            try:
                results = self._chat_store.search_conversations(
                    self._project_user_id,
                    **search_kwargs,
                )
            except TypeError:
                search_kwargs.pop("include_friend", None)
                results = self._chat_store.search_conversations(
                    self._project_user_id,
                    **search_kwargs,
                )
        except Exception as error:
            raise AgentToolError(f"Failed to search conversations: {error}") from error
        if results:
            hint = (
                "Use ReadProjectConversation(conversation_id=..., around_sequence=...) "
                "to inspect the messages centered around matched_sequence."
            )
        elif effective_scope == "project":
            hint = (
                "No conversations matched in the current project. "
                "Call SearchConversations(query=..., scope='all') to search across all your conversations."
            )
        else:
            hint = "No matching conversations found."
        body = {
            "query": clean_query,
            "scope": effective_scope,
            "project_id": active_project_id if effective_scope == "project" else None,
            "mode": clean_mode,
            "count": len(results),
            "results": results,
            "hint": hint,
        }
        return {
            "summary": f"{len(results)} conversa(s) encontrada(s) para '{clean_query}'",
            "content": json.dumps(body, ensure_ascii=False),
            "payload": {
                "query": clean_query,
                "scope": effective_scope,
                "mode": clean_mode,
                "project_id": active_project_id,
                "results": results,
                "tool_kind": "project",
                "project_action": "search_conversations",
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
                "around_sequence": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Optional message sequence number (e.g. matched_sequence from SearchConversations) to read messages centered around that point instead of only the end of the conversation.",
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
    ToolSpec(
        key="search_conversations",
        description="Search past conversations of this user (within the current project or across all conversations) by keywords in titles, messages, and compacted summaries.",
        legacy_schema=_schema(
            {
                "query": {
                    **_TEXT,
                    "description": "Keywords or phrase to search for across conversation titles, messages, and summaries.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["project", "all"],
                    "description": "Search scope: 'project' (default when in a project) or 'all' (all conversations of this user).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["chat", "code", "friend"],
                    "description": "Optional conversation mode filter ('chat', 'code', or 'friend').",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25,
                    "description": "Maximum number of matching conversations to return (default 8, max 25).",
                },
            },
            ("query",),
        ),
        handler="search_conversations",
        kind="project",
        read_only=True,
        available=lambda self: (self._chat_store is not None and bool(self._project_user_id)),
        order=595,
    ),
    ToolSpec(
        key="manage_session",
        description=(
            "List, inspect, start, send messages to, respond to pending prompts/permissions/plans in, "
            "or stop any Orin Chat ('chat') or Orin Code ('code') session across this user's projects."
        ),
        legacy_schema=_schema(
            {
                "action": {
                    "type": "string",
                    "enum": ["list", "inspect", "start", "send_message", "respond", "stop"],
                    "description": "Operation to perform: 'list', 'inspect', 'start', 'send_message', 'respond', or 'stop'.",
                },
                "conversation_id": {
                    **_TEXT,
                    "description": "Target session conversation_id (required for inspect, send_message, respond, stop).",
                },
                "code_conversation_id": {
                    **_TEXT,
                    "description": "Alias for conversation_id.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["chat", "code"],
                    "description": "Session mode ('chat' or 'code'). Defaults to 'code' for start; optional filter for list.",
                },
                "project_id": {
                    **_TEXT,
                    "description": "Target project ID or exact project name (for start or list filter).",
                },
                "workspace_path": {
                    **_TEXT,
                    "description": "Optional local directory path for action='start'.",
                },
                "title": {
                    **_TEXT,
                    "description": "Session title for action='start'.",
                },
                "prompt": {
                    **_TEXT,
                    "description": "Task prompt or message to send (for action='start', 'send_message', or 'respond').",
                },
                "message": {
                    **_TEXT,
                    "description": "Follow-up message or answer text (for action='send_message' or 'respond').",
                },
                "provider": {
                    **_TEXT,
                    "description": "Optional provider slug (e.g. 'anthropic', 'openai-codex', 'google-antigravity', 'openrouter', 'openai', 'ollama').",
                },
                "model_id": {
                    **_TEXT,
                    "description": "Optional model ID for action='start' or 'send_message'.",
                },
                "permission_mode": {
                    "type": "string",
                    "enum": ["full_autonomy", "accept_edits", "plan_mode", "ask_permissions"],
                    "description": "Permission mode for action='start' or 'send_message'.",
                },
                "state": {
                    **_TEXT,
                    "description": "Optional state filter for action='list' ('running', 'waiting_user', 'completed', 'failed').",
                },
                "decision": {
                    "type": "string",
                    "enum": ["approve", "reject", "revise", "allow_once", "allow_always", "deny"],
                    "description": "Decision for action='respond' when resolving a plan ('approve'/'reject'/'revise') or tool permission ('allow_once'/'allow_always'/'deny').",
                },
                "feedback": {
                    **_TEXT,
                    "description": "Optional rationale, revision notes, or answer text for action='respond'.",
                },
                "request_id": {
                    **_TEXT,
                    "description": "Optional permission request ID for action='respond' when resolving a tool permission.",
                },
                "answers": {
                    "description": "Structured or text answers for action='respond' when answering AskUserQuestion.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum number of sessions to return in action='list' (default 20).",
                },
                "message_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of recent messages to return in action='inspect' (default 10).",
                },
            },
            ("action",),
        ),
        handler="manage_session",
        kind="session_management",
        available=lambda self: (not self._code_mode_active and self._chat_store is not None and bool(self._project_user_id)),
        order=552,
    ),
)
