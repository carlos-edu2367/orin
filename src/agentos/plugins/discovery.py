from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .sources import SourceRejected, resolve_source

CACHE_TTL = timedelta(minutes=15)
MANIFEST_CACHE_TTL = timedelta(hours=24)
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
    installable_kind: str  # "plugin" | "mcp_raw" | "unknown"


class PluginDiscoveryService:
    """Surfaces installable plugin candidates; never fetches or clones anything itself."""

    def __init__(self, plugin_service: Any, *, search_client: Any = None, manifest_probe: Any = None) -> None:
        self._plugin_service = plugin_service
        self._search_client = search_client
        self._manifest_probe = manifest_probe
        # Per person: the library lists the person's own marketplaces (W5-D1), so one cache would leak them.
        self._cache: dict[str, tuple[tuple[list[PluginLibraryEntry], bool], datetime]] = {}
        self._manifest_cache: dict[str, tuple[bool | None, datetime]] = {}

    def entries(self, *, user_id: str, refresh: bool = False, query: str | None = None) -> tuple[list[PluginLibraryEntry], bool]:
        needle = (query or "").strip()
        if needle:
            # An explicit query always searches live — it's cheap (one registry
            # scan, one web call) and caching per free-text query isn't worth it.
            registry = self._registry_entries(needle, user_id)
            web, web_available = self._web_entries((needle,), limit=8)
            return self._merge(registry, web), web_available
        cached = self._cache.get(user_id)
        if not refresh and cached is not None and _now() - cached[1] < CACHE_TTL:
            return cached[0]
        registry = self._registry_entries("", user_id)
        web, web_available = self._web_entries(DISCOVERY_QUERIES, limit=5)
        result = (self._merge(registry, web), web_available)
        if len(self._cache) >= 256:  # bounded: drop the oldest person's entry
            self._cache.pop(min(self._cache, key=lambda key: self._cache[key][1]))
        self._cache[user_id] = (result, _now())
        return result

    def _registry_entries(self, needle: str, user_id: str) -> list[PluginLibraryEntry]:
        entries: list[PluginLibraryEntry] = []
        for item in self._plugin_service.search(needle, user_id=user_id):
            try:
                source = resolve_source(str(item["reference"]))
            except SourceRejected:
                continue
            if source.kind != "git" or not source.url:
                # Local-path and bare-name registry entries aren't installable via a
                # public reference; skip them rather than leaking a raw filesystem
                # path or unresolved name into the API response.
                continue
            entries.append(PluginLibraryEntry(str(item["name"]), str(item.get("description") or ""), source.url, "registry", "plugin"))
        return entries

    def _web_entries(self, queries: tuple[str, ...], *, limit: int) -> tuple[list[PluginLibraryEntry], bool]:
        if self._search_client is None:
            return [], False
        entries: list[PluginLibraryEntry] = []
        for query in queries:
            try:
                results = self._search_client.search(query, limit=limit)
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
                kind = self._probe_kind(source.url)
                entries.append(PluginLibraryEntry(source.suggested_name or result.title, result.snippet, source.url, "web", kind))
        return entries, True

    def _probe_kind(self, repo_url: str) -> str:
        if self._manifest_probe is None:
            return "unknown"
        cached = self._manifest_cache.get(repo_url)
        if cached is not None and _now() - cached[1] < MANIFEST_CACHE_TTL:
            has_manifest = cached[0]
        else:
            owner_repo = repo_url.removeprefix("https://github.com/").removesuffix(".git")
            owner, _, repo = owner_repo.partition("/")
            has_manifest = self._manifest_probe.probe(owner, repo) if owner and repo else None
            self._manifest_cache[repo_url] = (has_manifest, _now())
        if has_manifest is True:
            return "plugin"
        if has_manifest is False:
            return "mcp_raw"
        return "unknown"

    @staticmethod
    def _merge(registry: list[PluginLibraryEntry], web: list[PluginLibraryEntry]) -> list[PluginLibraryEntry]:
        seen: dict[str, PluginLibraryEntry] = {}
        for entry in registry:
            seen[entry.source_url.casefold()] = entry
        for entry in web:
            seen.setdefault(entry.source_url.casefold(), entry)
        return list(seen.values())


__all__ = ["PluginDiscoveryService", "PluginLibraryEntry"]
