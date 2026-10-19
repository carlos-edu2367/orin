"""Memory tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from typing import Any

from agentos.agentic.tools._shared import AgentToolError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec


class MemoryTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def remember(self, fact: str, tags: list[str] | None = None) -> dict[str, Any]:
        if self.memory is None:
            raise AgentToolError("Memory is not available.")
        if not isinstance(fact, str) or not fact.strip():
            raise AgentToolError("fact must be a non-blank string")
        record = self.memory.save(fact.strip()[:2000], tuple(str(tag)[:40] for tag in (tags or []))[:8])
        return {
            "summary": f"Guardou: {fact.strip()[:60]}",
            "content": f"Saved memory {record['memory_id']}.",
            "payload": {"memory_id": record["memory_id"], "label": fact.strip()[:80]},
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
        description="Save a durable fact about the user or the project for future conversations.",
        legacy_schema=_schema({"fact": _TEXT, "tags": {"type": "array", "items": _TEXT}}, ("fact",)),
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
