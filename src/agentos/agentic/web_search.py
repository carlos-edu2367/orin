"""Web search for the conversational agent.

``fetch_url`` can only read an address the agent already knows. This module is
the discovery half: a small, injectable client whose response is projected into
bounded values before anything reaches the model. The API key travels in a
header and is never part of a URL, a result or an activity payload.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping

import httpx

from .agent_tools import AgentToolError, _public_url


SEARCH_TIMEOUT_SECONDS = 15
MAX_SEARCH_RESULTS = 10
MAX_TITLE_CHARS = 200
MAX_SNIPPET_CHARS = 400
API_KEY_VARIABLE = "AGENTOS_SEARCH_API_KEY"
ENDPOINT_VARIABLE = "AGENTOS_SEARCH_ENDPOINT"
DEFAULT_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class BraveSearchClient:
    """Brave Search API adapter; any provider with the same shape can replace it."""

    def __init__(self, api_key: str, client: httpx.Client | None = None, *, endpoint: str = DEFAULT_ENDPOINT) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be a non-blank string")
        self._api_key = api_key
        self._endpoint = _public_url(endpoint)
        self._client = client or httpx.Client(timeout=SEARCH_TIMEOUT_SECONDS)
        self._owns_client = client is None

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        bounded = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        response = self._client.get(
            self._endpoint,
            params={"q": str(query)[:400], "count": bounded},
            headers={"x-subscription-token": self._api_key, "accept": "application/json"},
        )
        response.raise_for_status()
        try:
            body = response.json()
        except (TypeError, ValueError):
            return []
        return [
            SearchResult(
                self.redact_text(result.title),
                self.redact_text(result.url),
                self.redact_text(result.snippet),
            )
            for result in self._project(body, bounded)
        ]

    @staticmethod
    def _project(body: Any, limit: int) -> list[SearchResult]:
        web = body.get("web") if isinstance(body, Mapping) else None
        entries = web.get("results") if isinstance(web, Mapping) else None
        if not isinstance(entries, list):
            return []
        results: list[SearchResult] = []
        for item in entries[:limit]:
            if not isinstance(item, Mapping):
                continue
            url = str(item.get("url") or "")
            try:
                url = _public_url(url)
            except AgentToolError:
                continue
            results.append(SearchResult(
                str(item.get("title") or url)[:MAX_TITLE_CHARS],
                url,
                str(item.get("description") or "")[:MAX_SNIPPET_CHARS],
            ))
        return results

    def redact_text(self, value: object) -> str:
        return str(value).replace(self._api_key, "[REDACTED]")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def search_client_from_environment() -> BraveSearchClient | None:
    """Build a client only when a key is configured; otherwise the tool is not registered."""
    key = os.environ.get(API_KEY_VARIABLE, "").strip()
    if not key:
        return None
    return BraveSearchClient(key, endpoint=os.environ.get(ENDPOINT_VARIABLE, "").strip() or DEFAULT_ENDPOINT)


__all__ = [
    "API_KEY_VARIABLE",
    "BraveSearchClient",
    "DEFAULT_ENDPOINT",
    "MAX_SEARCH_RESULTS",
    "SearchResult",
    "search_client_from_environment",
]
