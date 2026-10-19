"""Search code tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import os
from typing import Any, Mapping

from agentos.agentic.tools._shared import AgentToolError, logger, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec


class CodeSearchTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def search_code(self, query: str, limit: int = 8) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise AgentToolError("query must be a non-blank string")
        try:
            result = self._retrieval.search(query, limit=max(1, min(int(limit), 20)))
        except Exception as error:  # noqa: BLE001 - retrieval degrades, it never fails a turn
            raise AgentToolError(f"code search is unavailable: {error}") from error
        if not result.hits:
            body = "[no match]"
        else:
            body = "\n\n".join(
                f"{hit.location}{f' — {hit.symbol}' if hit.symbol else ''}\n{hit.text}"
                for hit in result.hits
            )
        if result.mode == "lexical":
            body = f"[lexical mode: no embedder available, results are keyword-based]\n\n{body}"
        return {
            "summary": f"Buscou no código '{query[:40]}': {len(result.hits)} {'trecho' if len(result.hits) == 1 else 'trechos'}",
            "content": body,
            "payload": {
                "query": query[:200], "count": len(result.hits), "mode": result.mode,
                "label": query[:80], "indexed_files": result.status.files, "indexed_chunks": result.status.chunks,
            },
        }

    def project_map(self, limit: int = 20) -> dict[str, Any]:
        try:
            entries = self._retrieval.project_map(limit=max(1, min(int(limit), 50)))
        except Exception as error:  # noqa: BLE001
            raise AgentToolError(f"the project map is unavailable: {error}") from error
        if not entries:
            body = "[the project index is empty]"
        else:
            body = "\n".join(
                f"{entry['path']} (imported by {entry['imported_by']}): {', '.join(entry['symbols']) or '—'}"
                for entry in entries
            )
        return {
            "summary": f"Mapeou {len(entries)} {'arquivo' if len(entries) == 1 else 'arquivos'} centrais",
            "content": body,
            "payload": {"count": len(entries), "label": "project map"},
        }

    def _queue_reindex(self, changed: list[dict[str, Any]]) -> None:
        """Feed the index from the artefact list a mutating tool already computed.

        This must never call ``RetrievalService.reindex`` directly: that method
        re-embeds every chunk in the project still missing a vector, not just the
        ones just written, and would block the tool call on however much of the
        initial scan has not finished yet. ``_retrieval_reindex`` is the
        background worker's non-blocking queue instead.
        """
        if self._retrieval_reindex is None or not changed:
            return
        paths = [str(item["path"]) for item in changed if isinstance(item, Mapping) and item.get("path")]
        if not paths:
            return
        try:
            self._retrieval_reindex(paths)
        except Exception:
            # Never breaks the tool call that ran, but a silent swallow here
            # would mean the index stops updating for the rest of the session
            # with zero trace anywhere — same risk IndexWorker._run logs for.
            logger.exception("could not queue a reindex for %s", paths)


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="search_code",
        description="Search the project by meaning, not by text. Use this when you do not know where something lives, or when you want to understand how a flow works. Returns whole definitions with path:line so you can open and confirm them.",
        legacy_schema=_schema({
            "query": {**_TEXT, "description": "What you are looking for, in plain language."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "How many results to return. Defaults to 8."},
        }, ("query",)),
        handler="search_code",
        kind="filesystem",
        read_only=True,
        policy_tags=lambda self: (("network",) if (os.getenv("ORIN_RETRIEVAL_EMBEDDER", "").strip().lower() == "remote") else ()),
        available=lambda self: (self._retrieval is not None),
        order=220,
    ),
    ToolSpec(
        key="project_map",
        description="List the files the project depends on most, with their top-level symbols. Use this once to orient yourself in an unfamiliar codebase.",
        legacy_schema=_schema({"limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
        handler="project_map",
        kind="filesystem",
        read_only=True,
        available=lambda self: (self._retrieval is not None),
        order=230,
    ),
)
