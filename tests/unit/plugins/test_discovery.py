from datetime import UTC, datetime, timedelta

import httpx

from agentos.agentic.web_search import SearchResult
from agentos.plugins.discovery import DISCOVERY_QUERIES, PluginDiscoveryService

REGISTRY = [{"name": "superpowers", "reference": "obra/superpowers", "description": "Skills de processo"}]


class FakeManifestProbe:
    def __init__(self, result_by_repo=None, *, error=False):
        self._result_by_repo = result_by_repo or {}
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def probe(self, owner, repo):
        self.calls.append((owner, repo))
        if self._error:
            return None
        return self._result_by_repo.get(f"{owner}/{repo}")


class FakePluginService:
    def __init__(self, entries):
        self._entries = entries
        self.search_calls = 0
        self.queries: list[str] = []

    def search(self, query):
        self.search_calls += 1
        self.queries.append(query)
        return self._entries


class FakeSearchClient:
    def __init__(self, results_by_query=None, *, error=False):
        self._results_by_query = results_by_query or {}
        self._error = error

    def search(self, query, *, limit=5):
        if self._error:
            raise httpx.HTTPError("boom")
        return self._results_by_query.get(query, [])


def test_registry_only_when_no_search_client():
    discovery = PluginDiscoveryService(FakePluginService(REGISTRY), search_client=None)
    entries, web_available = discovery.entries()
    assert web_available is False
    assert [e.origin for e in entries] == ["registry"]
    assert entries[0].source_url == "https://github.com/obra/superpowers.git"


def test_merges_web_results_and_registry_wins_on_conflict():
    results = {
        DISCOVERY_QUERIES[0]: [SearchResult("Superpowers", "https://github.com/obra/superpowers", "web desc")],
        DISCOVERY_QUERIES[1]: [SearchResult("Other MCP", "https://github.com/acme/other-mcp", "another plugin")],
    }
    discovery = PluginDiscoveryService(FakePluginService(REGISTRY), search_client=FakeSearchClient(results))
    entries, web_available = discovery.entries()
    assert web_available is True
    by_url = {e.source_url: e for e in entries}
    assert by_url["https://github.com/obra/superpowers.git"].origin == "registry"
    assert by_url["https://github.com/acme/other-mcp.git"].origin == "web"
    assert len(entries) == 2


def test_web_search_failure_keeps_registry_results_and_stays_available():
    discovery = PluginDiscoveryService(FakePluginService(REGISTRY), search_client=FakeSearchClient(error=True))
    entries, web_available = discovery.entries()
    assert web_available is True
    assert [e.origin for e in entries] == ["registry"]


def test_registry_entries_that_are_not_installable_git_sources_are_skipped(tmp_path):
    local_dir = tmp_path / "local-plugin"
    local_dir.mkdir()
    registry = [
        {"name": "local", "reference": str(local_dir), "description": "a local path, must not leak"},
        {"name": "bare-name", "reference": "not-a-repo-reference", "description": "no owner/repo shape"},
        {"name": "superpowers", "reference": "obra/superpowers", "description": "Skills de processo"},
    ]
    discovery = PluginDiscoveryService(FakePluginService(registry), search_client=None)
    entries, _ = discovery.entries()
    assert [e.name for e in entries] == ["superpowers"]


def test_results_are_cached_until_refresh():
    plugin_service = FakePluginService(REGISTRY)
    discovery = PluginDiscoveryService(plugin_service, search_client=None)
    discovery.entries()
    discovery.entries()
    assert plugin_service.search_calls == 1
    discovery.entries(refresh=True)
    assert plugin_service.search_calls == 2


def test_a_query_searches_directly_instead_of_the_fixed_topics():
    plugin_service = FakePluginService(REGISTRY)
    results = {"obsidian": [SearchResult("acme/obsidian-thing", "https://github.com/acme/obsidian-thing", "an obsidian plugin")]}
    discovery = PluginDiscoveryService(plugin_service, search_client=FakeSearchClient(results))
    entries, web_available = discovery.entries(query="obsidian")
    assert plugin_service.queries[-1] == "obsidian"
    assert web_available is True
    assert "https://github.com/acme/obsidian-thing.git" in [e.source_url for e in entries]


def test_a_query_bypasses_the_cached_default_listing():
    plugin_service = FakePluginService(REGISTRY)
    discovery = PluginDiscoveryService(plugin_service, search_client=None)
    discovery.entries()
    assert plugin_service.search_calls == 1
    discovery.entries(query="obsidian")
    assert plugin_service.search_calls == 2
    assert plugin_service.queries[-1] == "obsidian"


def test_a_blank_query_falls_back_to_the_cached_default_listing():
    plugin_service = FakePluginService(REGISTRY)
    discovery = PluginDiscoveryService(plugin_service, search_client=None)
    discovery.entries()
    discovery.entries(query="   ")
    assert plugin_service.search_calls == 1


def test_registry_entries_are_always_plugin_kind_and_skip_the_probe():
    probe = FakeManifestProbe({"obra/superpowers": False})
    discovery = PluginDiscoveryService(FakePluginService(REGISTRY), search_client=None, manifest_probe=probe)
    entries, _ = discovery.entries()
    assert entries[0].installable_kind == "plugin"
    assert probe.calls == []


def test_web_entries_are_tagged_plugin_or_mcp_raw_from_the_probe():
    results = {
        DISCOVERY_QUERIES[0]: [SearchResult("Has Manifest", "https://github.com/acme/has-manifest", "d")],
        DISCOVERY_QUERIES[1]: [SearchResult("No Manifest", "https://github.com/acme/no-manifest", "d")],
    }
    probe = FakeManifestProbe({"acme/has-manifest": True, "acme/no-manifest": False})
    discovery = PluginDiscoveryService(FakePluginService([]), search_client=FakeSearchClient(results), manifest_probe=probe)
    entries, _ = discovery.entries()
    by_url = {e.source_url: e for e in entries}
    assert by_url["https://github.com/acme/has-manifest.git"].installable_kind == "plugin"
    assert by_url["https://github.com/acme/no-manifest.git"].installable_kind == "mcp_raw"


def test_web_entries_are_unknown_kind_when_the_probe_is_disabled_or_fails():
    results = {DISCOVERY_QUERIES[0]: [SearchResult("Web Thing", "https://github.com/acme/web-thing", "d")]}
    no_probe = PluginDiscoveryService(FakePluginService([]), search_client=FakeSearchClient(results), manifest_probe=None)
    entries, _ = no_probe.entries()
    assert entries[0].installable_kind == "unknown"

    failing_probe = FakeManifestProbe(error=True)
    with_failing_probe = PluginDiscoveryService(FakePluginService([]), search_client=FakeSearchClient(results), manifest_probe=failing_probe)
    entries, _ = with_failing_probe.entries()
    assert entries[0].installable_kind == "unknown"


def test_manifest_probe_result_is_cached_for_24_hours():
    results = {DISCOVERY_QUERIES[0]: [SearchResult("Web Thing", "https://github.com/acme/web-thing", "d")]}
    probe = FakeManifestProbe({"acme/web-thing": True})
    discovery = PluginDiscoveryService(FakePluginService([]), search_client=FakeSearchClient(results), manifest_probe=probe)
    discovery.entries(refresh=True)
    discovery.entries(refresh=True)
    assert probe.calls == [("acme", "web-thing")]


def test_manifest_probe_cache_expires_after_24_hours():
    results = {DISCOVERY_QUERIES[0]: [SearchResult("Web Thing", "https://github.com/acme/web-thing", "d")]}
    probe = FakeManifestProbe({"acme/web-thing": True})
    discovery = PluginDiscoveryService(FakePluginService([]), search_client=FakeSearchClient(results), manifest_probe=probe)
    discovery.entries(refresh=True)
    stale_url = "https://github.com/acme/web-thing.git"
    discovery._manifest_cache[stale_url] = (discovery._manifest_cache[stale_url][0], datetime.now(UTC) - timedelta(hours=25))
    discovery.entries(refresh=True)
    assert probe.calls == [("acme", "web-thing"), ("acme", "web-thing")]
