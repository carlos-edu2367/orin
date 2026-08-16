from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .sources import SourceRejected, resolve_source

CACHE_TTL = timedelta(minutes=15)
DISCOVERY_QUERIES = (
    "topic:mcp-server stars:>=3",
    "topic:claude-plugin stars:>=3",
)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PluginLibraryEntry:
    name: str
    description: str
    source_url: str
    origin: str  # "registry" | "web"


class PluginDiscoveryService:
    """Surfaces installable plugin candidates; never fetches or clones anything itself."""

    def __init__(self, plugin_service: Any, *, search_client: Any = None) -> None:
        self._plugin_service = plugin_service
        self._search_client = search_client
        self._cache: tuple[list[PluginLibraryEntry], bool] | None = None
        self._cache_at: datetime | None = None

    def entries(self, *, refresh: bool = False) -> tuple[list[PluginLibraryEntry], bool]:
        if not refresh and self._cache is not None and self._cache_at is not None and _now() - self._cache_at < CACHE_TTL:
            return self._cache
        registry = self._registry_entries()
        web, web_available = self._web_entries()
        merged = self._merge(registry, web)
        self._cache, self._cache_at = (merged, web_available), _now()
        return self._cache

    def _registry_entries(self) -> list[PluginLibraryEntry]:
        entries: list[PluginLibraryEntry] = []
        for item in self._plugin_service.search(""):
            try:
                source = resolve_source(str(item["reference"]))
            except SourceRejected:
                continue
            if source.kind != "git" or not source.url:
                # Local-path and bare-name registry entries aren't installable via a
                # public reference; skip them rather than leaking a raw filesystem
                # path or unresolved name into the API response.
                continue
            entries.append(PluginLibraryEntry(str(item["name"]), str(item.get("description") or ""), source.url, "registry"))
        return entries

    def _web_entries(self) -> tuple[list[PluginLibraryEntry], bool]:
        if self._search_client is None:
            return [], False
        entries: list[PluginLibraryEntry] = []
        for query in DISCOVERY_QUERIES:
            try:
                results = self._search_client.search(query, limit=5)
            except httpx.HTTPError:
                # A configured client that failed this round is still "available" —
                # the frontend note is only for the no-client case.
                continue
            for result in results:
                try:
                    source = resolve_source(result.url)
                except SourceRejected:
                    continue
                if source.kind != "git" or not source.url:
                    continue
                entries.append(PluginLibraryEntry(source.suggested_name or result.title, result.snippet, source.url, "web"))
        return entries, True

    @staticmethod
    def _merge(registry: list[PluginLibraryEntry], web: list[PluginLibraryEntry]) -> list[PluginLibraryEntry]:
        seen: dict[str, PluginLibraryEntry] = {}
        for entry in registry:
            seen[entry.source_url.casefold()] = entry
        for entry in web:
            seen.setdefault(entry.source_url.casefold(), entry)
        return list(seen.values())


__all__ = ["PluginDiscoveryService", "PluginLibraryEntry"]
