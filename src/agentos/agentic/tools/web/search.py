"""Search tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from typing import Any
import httpx
from agentos.agentic.tools._shared import (
    AgentToolError,
    _public_url,
)


class WebSearchTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def web_search(self, query: str, limit: int = 5) -> dict[str, Any]:
        if self._search_client is None:
            raise AgentToolError("Web search is not available.")
        if not isinstance(query, str) or not query.strip():
            raise AgentToolError("query must be a non-blank string")
        try:
            redact = getattr(self._search_client, "redact_text", str)
            clean_query = str(redact(query.strip()))
            results = self._search_client.search(query.strip(), limit=int(limit))
            safe_results: list[tuple[str, str, str]] = []
            for item in results or ():
                try:
                    url = _public_url(str(item.url))
                except (AgentToolError, TypeError, ValueError):
                    continue
                safe_results.append((
                    str(redact(item.title)),
                    str(redact(url)),
                    str(redact(item.snippet)),
                ))
            if not safe_results:
                return {"summary": f"Nenhum resultado para '{clean_query[:40]}'", "content": "[no results]", "payload": {"count": 0, "label": clean_query[:80]}}
            body = "\n\n".join("\n".join(item) for item in safe_results)
            return {
                "summary": f"Buscou na web: {len(safe_results)} {'resultado' if len(safe_results) == 1 else 'resultados'}",
                "content": body,
                "payload": {"count": len(safe_results), "label": clean_query[:80]},
            }
        except httpx.HTTPError as error:
            raise AgentToolError(f"The search provider could not be reached: {type(error).__name__}") from error
        except Exception:  # noqa: BLE001 - provider failures must not reach the model
            raise AgentToolError("The search provider returned an invalid response.") from None
