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
        building = _index_building_note(self._retrieval, result.status.files)
        if building:
            body = f"{building}\n\n{body}"
        return {
            "summary": f"Buscou no código '{query[:40]}': {len(result.hits)} {'trecho' if len(result.hits) == 1 else 'trechos'}",
            "content": body,
            "payload": {
                "query": query[:200], "count": len(result.hits), "mode": result.mode,
                "label": query[:80], "indexed_files": result.status.files, "indexed_chunks": result.status.chunks,
            },
        }

    def _active_focus_paths(self) -> tuple[str, ...]:
        state = getattr(self, "_read_file_state", None)
        if not isinstance(state, Mapping) or not state:
            return ()
        lock = getattr(self, "_read_file_lock", None)
        if lock is not None:
            with lock:
                pairs = list(state.items())
        else:
            pairs = list(state.items())
        pairs.sort(key=lambda item: int(getattr(item[1], "read_seq", 0) or 0), reverse=True)
        workspace = getattr(self, "workspace", None)
        focused: list[str] = []
        seen: set[str] = set()
        for raw_path, _record in pairs:
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            normalized = raw_path.strip().replace("\\", "/")
            if workspace is not None:
                try:
                    resolved = workspace.resolve(raw_path)
                    normalized = workspace.key(resolved).replace("\\", "/")
                except Exception:
                    pass
            while normalized.startswith("./"):
                normalized = normalized[2:]
            normalized = normalized.lstrip("/")
            if normalized and not normalized.startswith(".orin/") and normalized not in seen:
                seen.add(normalized)
                focused.append(normalized)
        return tuple(focused)

    def project_map(
        self,
        limit: int = 20,
        query: str | None = None,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        if isinstance(limit, bool):
            raise AgentToolError("limit must be an integer between 1 and 50.")
        try:
            capped_limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError) as error:
            raise AgentToolError("limit must be an integer between 1 and 50.") from error
        if isinstance(max_tokens, bool):
            raise AgentToolError("max_tokens must be an integer between 100 and 8000.")
        try:
            capped_tokens = max(100, min(int(max_tokens), 8000))
        except (TypeError, ValueError) as error:
            raise AgentToolError("max_tokens must be an integer between 100 and 8000.") from error
        focus_paths = self._active_focus_paths()
        clean_query = str(query).strip() if isinstance(query, str) and query.strip() else ""
        try:
            try:
                entries = self._retrieval.project_map(
                    limit=capped_limit,
                    focus_paths=focus_paths,
                    query=clean_query,
                    max_tokens=capped_tokens,
                )
            except TypeError:
                entries = self._retrieval.project_map(limit=capped_limit)
        except Exception as error:  # noqa: BLE001
            raise AgentToolError(f"the project map is unavailable: {error}") from error
        if not entries:
            body = "[the project index is empty]"
        else:
            lines: list[str] = []
            for entry in entries:
                formatted = entry.get("formatted_symbols")
                if isinstance(formatted, (list, tuple)) and formatted:
                    sym_text = ", ".join(str(item) for item in formatted)
                elif isinstance(entry.get("symbol_lines"), (list, tuple)) and entry.get("symbol_lines"):
                    sym_text = ", ".join(f"{sym}:{line}" for sym, line in entry["symbol_lines"])
                else:
                    syms = entry.get("symbols") or ()
                    sym_text = ", ".join(str(item) for item in syms)
                lines.append(f"{entry['path']} (imported by {entry['imported_by']}): {sym_text or '—'}")
            body = "\n".join(lines)
        building = _index_building_note(self._retrieval, None)
        if building:
            body = f"{building}\n\n{body}"
        return {
            "summary": f"Mapeou {len(entries)} {'arquivo' if len(entries) == 1 else 'arquivos'} centrais",
            "content": body,
            "payload": {
                "count": len(entries),
                "label": "project map",
                "focus_files": len(focus_paths),
                "max_tokens": capped_tokens,
            },
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


def _index_building_note(retrieval: Any, files: int | None) -> str:
    """Say so when results come from an index the background scan is still filling."""
    try:
        if getattr(retrieval, "indexing", False) is not True:
            return ""
        if files is None:
            files = int(retrieval.status().files)
    except Exception:  # noqa: BLE001
        return ""
    return (
        f"[the project index is still being built in the background ({files} files so far); "
        "results may be partial — use glob_files/search_files for anything missing]"
    )


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
        legacy_schema=_schema({
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "query": {**_TEXT, "description": "Optional task or identifier query to personalize the map."},
            "max_tokens": {"type": "integer", "minimum": 100, "maximum": 8000, "description": "Token budget for the rendered map (default 1200)."},
        }),
        handler="project_map",
        kind="filesystem",
        read_only=True,
        available=lambda self: (self._retrieval is not None),
        order=230,
    ),
)
