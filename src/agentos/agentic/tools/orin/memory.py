"""Memory tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec
from agentos.memory.provenance import build_write_context_from_turn
from agentos.memory.quarantine import is_quarantine_enabled


_VALID_MEMORY_SCOPES = frozenset({"user", "project", "agent"})
_VALID_MEMORY_KINDS = frozenset({"fact", "preference", "architecture", "operational", "episodic"})
_USER_SCOPE_TAGS = frozenset({"user", "global", "perfil", "pessoal", "preferencia", "preferência", "meta", "metas"})
_MAX_MEMORY_FACT_CHARS = 4_000


class MemoryTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def _active_agent_memory_scope(self) -> Any | None:
        return getattr(self.memory, "agent_scope", None) or getattr(self, "_agent_memory_scope", None)

    def _build_memory_write_state(self) -> tuple[dict[str, Any], Any]:
        """Collect runtime-verified turn provenance state; never trusts model arguments."""
        provider = getattr(self, "_memory_turn_state_provider", None)
        state: dict[str, Any] = {}
        if callable(provider):
            try:
                raw_state = provider()
                if isinstance(raw_state, Mapping):
                    state.update(raw_state)
            except Exception:  # noqa: BLE001 - provenance provider error fails closed
                pass
        # Never allow any spoofed provenance fields even if accidentally present in a dict.
        for forbidden in ("source_kind", "trust"):
            state.pop(forbidden, None)

        merged_tools: list[Any] = list(state.get("tool_calls") or ())
        turn_tools = getattr(self, "_turn_tool_history", None) or ()
        merged_tools.extend(turn_tools)
        state["tool_calls"] = merged_tools

        if not state.get("user_message"):
            fallback_msg = getattr(self, "_current_user_message", None)
            if isinstance(fallback_msg, str) and fallback_msg.strip():
                state["user_message"] = fallback_msg
        if not state.get("conversation_id"):
            state["conversation_id"] = getattr(self, "conversation_id", None) or getattr(self, "_conversation_id", None)
        if not state.get("turn_id"):
            state["turn_id"] = getattr(self, "turn_id", None) or getattr(self, "_turn_id", None)
        if not state.get("workspace_root"):
            ws = getattr(self, "workspace", None)
            root = getattr(ws, "root", None) if ws is not None else None
            if root is not None:
                state["workspace_root"] = str(root)
        if not state.get("read_files"):
            read_state = getattr(self, "_read_file_state", None)
            if isinstance(read_state, Mapping) and read_state:
                state["read_files"] = tuple(str(p) for p in read_state.keys())

        effective_settings = state.get("effective_settings")
        if effective_settings is None:
            effective_settings = getattr(self, "_effective_settings", None)
        return state, effective_settings

    def remember(
        self,
        fact: str,
        tags: list[str] | None = None,
        scope: str | None = None,
        kind: str | None = None,
        replaces_memory_id: str | None = None,
        **_ignored: Any,
    ) -> dict[str, Any]:
        # Security invariant (WS-D4): any provenance keys injected by the model
        # (e.g. source_kind="user", trust=2) are strictly ignored.
        _ = _ignored
        if self.memory is None:
            raise AgentToolError("Memory is not available.")
        if not isinstance(fact, str) or not fact.strip():
            raise AgentToolError("fact must be a non-blank string")
        agent_scope = self._active_agent_memory_scope()
        normalized_scope: str | None = None
        if scope is not None and str(scope).strip():
            candidate_scope = str(scope).strip().lower()
            if candidate_scope == "agent" and agent_scope is None:
                raise AgentToolError(
                    "scope='agent' is only available inside a subagent with agent memory enabled "
                    "(memory.agent.enabled=true and agent memory != 'none'). Use scope='user' or scope='project'."
                )
            if candidate_scope not in _VALID_MEMORY_SCOPES:
                allowed_msg = (
                    "scope must be 'user', 'project' or 'agent'"
                    if agent_scope is not None
                    else "scope must be 'user' or 'project'"
                )
                raise AgentToolError(allowed_msg)
            normalized_scope = candidate_scope
        if agent_scope is not None:
            if normalized_scope is None:
                normalized_scope = "agent"
            elif normalized_scope != "agent":
                raise AgentToolError(
                    "Parent 'user' and 'project' memory scopes are read-only inside this subagent; use scope='agent'."
                )
        normalized_kind: str | None = None
        if kind is not None and str(kind).strip():
            candidate_kind = str(kind).strip().lower()
            if candidate_kind not in _VALID_MEMORY_KINDS:
                raise AgentToolError(
                    "kind must be 'fact', 'preference', 'architecture', 'operational' or 'episodic'"
                )
            normalized_kind = candidate_kind
        clean_fact = fact.strip()[:_MAX_MEMORY_FACT_CHARS]
        clean_tags = tuple(str(tag)[:40] for tag in (tags or []))[:8]
        if agent_scope is None and normalized_scope is None and (
            normalized_kind == "preference"
            or any(tag.strip().lower() in _USER_SCOPE_TAGS for tag in clean_tags)
        ):
            normalized_scope = "user"

        turn_state, effective_settings = self._build_memory_write_state()
        write_context = build_write_context_from_turn(
            turn_state,
            agent_key=(agent_scope.agent_key if agent_scope is not None else None),
        )
        quarantine_enabled = is_quarantine_enabled(effective_settings, default=True)

        save_kwargs: dict[str, Any] = {
            "context": write_context,
            "quarantine_enabled": quarantine_enabled,
        }
        if normalized_scope is not None:
            save_kwargs["scope_type"] = normalized_scope
        if normalized_kind is not None:
            save_kwargs["kind"] = normalized_kind
        if replaces_memory_id is not None and str(replaces_memory_id).strip():
            save_kwargs["supersedes_id"] = str(replaces_memory_id).strip()
        try:
            try:
                record = self.memory.save(clean_fact, clean_tags, **save_kwargs)
            except TypeError:
                save_kwargs.pop("quarantine_enabled", None)
                save_kwargs.pop("context", None)
                try:
                    record = self.memory.save(clean_fact, clean_tags, **save_kwargs)
                except TypeError:
                    save_kwargs.pop("supersedes_id", None)
                    try:
                        record = self.memory.save(clean_fact, clean_tags, **save_kwargs)
                    except TypeError:
                        record = self.memory.save(clean_fact, clean_tags)
        except PermissionError as exc:
            raise AgentToolError(str(exc)) from exc

        status = str(record.get("status") or "active")
        payload: dict[str, Any] = {
            "memory_id": record["memory_id"],
            "label": clean_fact[:80],
            "superseded": list(record.get("superseded") or []),
            "status": status,
            "source_kind": str(record.get("source_kind") or write_context.source_kind),
            "source_ref": record.get("source_ref", write_context.source_ref),
            "trust": int(record["trust"]) if record.get("trust") is not None else write_context.trust,
        }
        if status == "quarantined":
            notifier = getattr(self, "_on_memory_quarantined", None)
            if callable(notifier):
                try:
                    notifier(
                        {
                            "memory_id": record["memory_id"],
                            "fact": clean_fact,
                            "kind": normalized_kind or "fact",
                            "scope": normalized_scope or ("project" if getattr(self, "_project_id", None) else "user"),
                            "project_id": getattr(self, "_project_id", None),
                            "status": "quarantined",
                            "source_kind": payload["source_kind"],
                            "source_ref": payload["source_ref"],
                            "trust": payload["trust"],
                        }
                    )
                except Exception:  # noqa: BLE001 - visible event callback never breaks tool return
                    pass
            return {
                "summary": f"Em quarentena: {clean_fact[:60]}",
                "content": (
                    f"Memória {record['memory_id']} salva em quarentena, aguardando aprovação do usuário "
                    f"(origem: {payload['source_kind']})."
                ),
                "payload": payload,
            }
        if status == "proposed" and record.get("decision") != "conflict":
            agent_key_str = str(
                record.get("agent_key")
                or (agent_scope.agent_key if agent_scope is not None else "")
            )
            payload["scope_type"] = str(record.get("scope_type") or "agent")
            if agent_key_str:
                payload["agent_key"] = agent_key_str
            return {
                "summary": f"Proposta salva ({agent_key_str or 'agente'}): {clean_fact[:60]}",
                "content": (
                    f"Memória {record['memory_id']} salva como proposta (status=proposed) no escopo do agente "
                    f"'{agent_key_str or 'agent'}'; será promovida após verificação aprovada ou aprovação do usuário."
                ),
                "payload": payload,
            }
        return {
            "summary": f"Guardou: {clean_fact[:60]}",
            "content": f"Saved memory {record['memory_id']}.",
            "payload": payload,
        }

    def recall(
        self,
        query: str | None = None,
        memory_id: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        if self.memory is None:
            raise AgentToolError("Memory is not available.")
        clean_query = str(query).strip() if query is not None else ""
        clean_memory_id = str(memory_id).strip() if memory_id is not None else ""
        if not clean_query and not clean_memory_id:
            raise AgentToolError("Provide 'query' or 'memory_id' to recall memories.")

        agent_scope = self._active_agent_memory_scope()
        normalized_scope: str | None = None
        if scope is not None and str(scope).strip():
            candidate_scope = str(scope).strip().lower()
            if candidate_scope == "agent" and agent_scope is None:
                raise AgentToolError(
                    "scope='agent' is only available inside a subagent with agent memory enabled "
                    "(memory.agent.enabled=true and agent memory != 'none'). Use scope='user' or scope='project'."
                )
            if candidate_scope not in _VALID_MEMORY_SCOPES:
                allowed_msg = (
                    "scope must be 'user', 'project' or 'agent'"
                    if agent_scope is not None
                    else "scope must be 'user' or 'project'"
                )
                raise AgentToolError(allowed_msg)
            normalized_scope = candidate_scope

        conv_id = getattr(self, "conversation_id", None) or getattr(self, "_conversation_id", None)

        if clean_memory_id:
            getter = getattr(self.memory, "get_memory", None)
            item: dict[str, Any] | None = None
            if callable(getter):
                try:
                    item = getter(clean_memory_id, conversation_id=conv_id)
                except TypeError:
                    item = getter(clean_memory_id)
            if not item:
                return {
                    "summary": "Nenhuma memória relevante",
                    "content": f"No stored memory matched memory_id '{clean_memory_id}'.",
                    "payload": {"count": 0, "memory_id": clean_memory_id, "label": clean_memory_id[:80]},
                }
            if item.get("status") == "deleted" or item.get("deleted") is True:
                return {
                    "summary": "Memória esquecida",
                    "content": f"Memory '{clean_memory_id}': memória esquecida.",
                    "payload": {
                        "count": 0,
                        "memory_id": clean_memory_id,
                        "status": "deleted",
                        "error": "memória esquecida",
                        "label": clean_memory_id[:80],
                    },
                }
            item_status = str(item.get("status") or "active").strip().lower()
            if item_status != "active":
                return {
                    "summary": "Nenhuma memória relevante",
                    "content": f"No stored memory matched memory_id '{clean_memory_id}'.",
                    "payload": {"count": 0, "memory_id": clean_memory_id, "label": clean_memory_id[:80]},
                }
            if normalized_scope is not None and str(item.get("scope_type") or "user") != normalized_scope:
                return {
                    "summary": "Nenhuma memória relevante",
                    "content": f"No stored memory matched memory_id '{clean_memory_id}' in scope '{normalized_scope}'.",
                    "payload": {"count": 0, "memory_id": clean_memory_id, "label": clean_memory_id[:80]},
                }
            tags_list = [str(t) for t in (item.get("tags") or ())]
            tags_str = ", ".join(tags_list) if tags_list else "none"
            kind_str = str(item.get("kind") or "fact")
            scope_str = str(item.get("scope_type") or "user")
            updated_str = str(item.get("updated_at") or "unknown")
            fact_text = str(item.get("fact") or "")
            body = (
                f"- [{item.get('memory_id', clean_memory_id)}] "
                f"(kind={kind_str}, scope={scope_str}, tags={tags_str}, updated_at={updated_str})\n"
                f"{fact_text}"
            )
            return {
                "summary": f"Recuperou memória {clean_memory_id[:24]}",
                "content": body,
                "payload": {
                    "count": 1,
                    "memory_id": str(item.get("memory_id", clean_memory_id)),
                    "kind": kind_str,
                    "scope": scope_str,
                    "tags": tags_list,
                    "updated_at": updated_str,
                    "label": clean_memory_id[:80],
                },
            }

        search_kwargs: dict[str, Any] = {"limit": 8}
        if normalized_scope is not None:
            search_kwargs["scope"] = normalized_scope
        if conv_id is not None:
            search_kwargs["conversation_id"] = str(conv_id)
        try:
            matches = self.memory.search(clean_query[:200], **search_kwargs)
        except TypeError:
            matches = self.memory.search(clean_query[:200], limit=8)
        if not matches:
            return {
                "summary": "Nenhuma memória relevante",
                "content": "No stored memory matched this query.",
                "payload": {"count": 0, "label": clean_query[:80]},
            }
        lines: list[str] = []
        for item in matches:
            mid = str(item.get("memory_id") or "").strip()
            fact_val = str(item.get("fact") or "")
            if mid:
                lines.append(f"- [{mid}] {fact_val}")
            else:
                lines.append(f"- {fact_val}")
        body = "\n".join(lines)
        return {
            "summary": f"Recuperou {len(matches)} {'memória' if len(matches) == 1 else 'memórias'}",
            "content": body,
            "payload": {"count": len(matches), "label": clean_query[:80]},
        }


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="remember",
        description=(
            "Save a durable fact about the user or the project for future conversations. "
            "Use scope='user' for personal facts, goals and cross-project preferences, "
            "or scope='project' for conventions, architecture and verified episodes of the current project."
        ),
        legacy_schema=_schema(
            {
                "fact": _TEXT,
                "tags": {"type": "array", "items": _TEXT},
                "scope": {
                    "type": "string",
                    "enum": ["user", "project"],
                    "description": "Where this memory applies: 'user' across all projects/conversations, or 'project' only in the active project.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["fact", "preference", "architecture", "operational", "episodic"],
                    "description": "Category of memory: 'preference' (how the user works), 'fact' (durable context), 'architecture' (codebase structure), 'operational' (commands/workflows), or 'episodic' (verified root cause or debugging episode).",
                },
                "replaces_memory_id": {
                    "type": "string",
                    "description": "Optional ID of an existing memory that this new fact directly updates or supersedes.",
                },
            },
            ("fact",),
        ),
        handler="remember",
        kind="memory",
        policy_tags=("mutates",),
        available=lambda self: (self.memory is not None),
        order=330,
    ),
    ToolSpec(
        key="recall",
        description=(
            "Search previously saved facts by query, or load the full text of an archival memory by memory_id."
        ),
        legacy_schema=_schema(
            {
                "query": _TEXT,
                "memory_id": {
                    "type": "string",
                    "description": "Exact memory_id (e.g. from an archival pointer in the prompt) to load its full untruncated content.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["user", "project"],
                    "description": "Optional scope filter ('user' or 'project').",
                },
            },
            (),
        ),
        handler="recall",
        kind="memory",
        read_only=True,
        available=lambda self: (self.memory is not None),
        order=340,
    ),
)
