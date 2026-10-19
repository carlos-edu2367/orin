"""Search code tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from typing import Any, Mapping
from agentos.agentic.tools._shared import (
    AgentToolError,
    logger,
)


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
