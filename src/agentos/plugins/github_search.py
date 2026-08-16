"""Keyless GitHub repository search used to discover installable plugins.

GitHub's repository search endpoint answers unauthenticated requests (rate
limited, but the discovery cache keeps calls infrequent), so plugin
discovery never depends on a paid search API key the way the conversational
agent's ``agentic.web_search`` tool does.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import httpx

SEARCH_TIMEOUT_SECONDS = 15
MAX_SEARCH_RESULTS = 10
DEFAULT_ENDPOINT = "https://api.github.com/search/repositories"


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class GithubRepositorySearchClient:
    """Searches public GitHub repositories; needs no credentials."""

    def __init__(self, client: httpx.Client | None = None, *, endpoint: str = DEFAULT_ENDPOINT) -> None:
        self._endpoint = endpoint
        self._client = client or httpx.Client(timeout=SEARCH_TIMEOUT_SECONDS)
        self._owns_client = client is None

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        bounded = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        response = self._client.get(
            self._endpoint,
            # Sorting by stars (rather than recency) matters here: repos that game
            # topic tags to advertise unrelated products tend to self-update
            # frequently to stay near the top of a "recently updated" sort.
            params={"q": str(query)[:400], "per_page": bounded, "sort": "stars"},
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "orin-plugin-discovery",
            },
        )
        response.raise_for_status()
        try:
            body = response.json()
        except (TypeError, ValueError):
            return []
        return self._project(body, bounded)

    @staticmethod
    def _project(body: Any, limit: int) -> list[SearchResult]:
        items = body.get("items") if isinstance(body, Mapping) else None
        if not isinstance(items, list):
            return []
        results: list[SearchResult] = []
        for item in items[:limit]:
            if not isinstance(item, Mapping):
                continue
            url = str(item.get("html_url") or "")
            if not url:
                continue
            results.append(SearchResult(str(item.get("full_name") or url), url, str(item.get("description") or "")[:400]))
        return results

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["DEFAULT_ENDPOINT", "GithubRepositorySearchClient", "SearchResult"]
