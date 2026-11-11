"""Memory tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from typing import Any

from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec


_VALID_MEMORY_SCOPES = frozenset({"user", "project"})
_VALID_MEMORY_KINDS = frozenset({"fact", "preference", "architecture", "operational"})
_USER_SCOPE_TAGS = frozenset({"user", "global", "perfil", "pessoal", "preferencia", "preferência", "meta", "metas"})


class MemoryTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def remember(
        self,
        fact: str,
        tags: list[str] | None = None,
        scope: str | None = None,
        kind: str | None = None,
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
                raise AgentToolError("kind must be 'fact', 'preference', 'architecture' or 'operational'")
            normalized_kind = candidate_kind
        clean_fact = fact.strip()[:2000]
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
        try:
            record = self.memory.save(clean_fact, clean_tags, **save_kwargs)
        except TypeError:
            record = self.memory.save(clean_fact, clean_tags)
        return {
            "summary": f"Guardou: {clean_fact[:60]}",
            "content": f"Saved memory {record['memory_id']}.",
            "payload": {"memory_id": record["memory_id"], "label": clean_fact[:80]},
        }

    def recall(self, query: str) -> dict[str, Any]:
        if self.memory is None:
            raise AgentToolError("Memory is not available.")
        matches = self.memory.search(str(query)[:200], limit=8)
        if not matches:
            return {"summary": "Nenhuma memória relevante", "content": "No stored memory matched this query.", "payload": {"count": 0, "label": str(query)[:80]}}
        body = "\n".join(f"- {item['fact']}" for item in matches)
        return {
            "summary": f"Recuperou {len(matches)} {'memória' if len(matches) == 1 else 'memórias'}",
            "content": body,
            "payload": {"count": len(matches), "label": str(query)[:80]},
        }


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="remember",
        description=(
            "Save a durable fact about the user or the project for future conversations. "
            "Use scope='user' for personal facts, goals and cross-project preferences, "
            "or scope='project' for conventions and architecture of the current project."
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
                    "enum": ["fact", "preference", "architecture", "operational"],
                    "description": "Category of memory: 'preference' (how the user works), 'fact' (durable context), 'architecture' (codebase structure), or 'operational' (commands/workflows).",
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
        description="Search previously saved facts.",
        legacy_schema=_schema({"query": _TEXT}, ("query",)),
        handler="recall",
        kind="memory",
        read_only=True,
        available=lambda self: (self.memory is not None),
        order=340,
    ),
)
