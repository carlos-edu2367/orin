"""Memory tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from typing import Any

from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec


_VALID_MEMORY_SCOPES = frozenset({"user", "project"})
_VALID_MEMORY_KINDS = frozenset({"fact", "preference", "architecture", "operational", "episodic"})
_USER_SCOPE_TAGS = frozenset({"user", "global", "perfil", "pessoal", "preferencia", "preferência", "meta", "metas"})
_MAX_MEMORY_FACT_CHARS = 4_000


class MemoryTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def remember(
        self,
        fact: str,
        tags: list[str] | None = None,
        scope: str | None = None,
        kind: str | None = None,
        replaces_memory_id: str | None = None,
    ) -> dict[str, Any]:
        if self.memory is None:
            raise AgentToolError("Memory is not available.")
        if not isinstance(fact, str) or not fact.strip():
            raise AgentToolError("fact must be a non-blank string")
        normalized_scope: str | None = None
        if scope is not None and str(scope).strip():
            candidate_scope = str(scope).strip().lower()
            if candidate_scope not in _VALID_MEMORY_SCOPES:
                raise AgentToolError("scope must be 'user' or 'project'")
            normalized_scope = candidate_scope
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
        if normalized_scope is None and (
            normalized_kind == "preference"
            or any(tag.strip().lower() in _USER_SCOPE_TAGS for tag in clean_tags)
        ):
            normalized_scope = "user"
        save_kwargs: dict[str, Any] = {}
        if normalized_scope is not None:
            save_kwargs["scope_type"] = normalized_scope
        if normalized_kind is not None:
            save_kwargs["kind"] = normalized_kind
        if replaces_memory_id is not None and str(replaces_memory_id).strip():
            save_kwargs["supersedes_id"] = str(replaces_memory_id).strip()
        try:
            record = self.memory.save(clean_fact, clean_tags, **save_kwargs)
        except TypeError:
            save_kwargs.pop("supersedes_id", None)
            try:
                record = self.memory.save(clean_fact, clean_tags, **save_kwargs)
            except TypeError:
                record = self.memory.save(clean_fact, clean_tags)
        return {
            "summary": f"Guardou: {clean_fact[:60]}",
            "content": f"Saved memory {record['memory_id']}.",
            "payload": {
                "memory_id": record["memory_id"],
                "label": clean_fact[:80],
                "superseded": list(record.get("superseded") or []),
            },
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

        normalized_scope: str | None = None
        if scope is not None and str(scope).strip():
            candidate_scope = str(scope).strip().lower()
            if candidate_scope not in _VALID_MEMORY_SCOPES:
                raise AgentToolError("scope must be 'user' or 'project'")
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
