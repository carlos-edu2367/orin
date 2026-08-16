import httpx

from agentos.agentic.web_search import SearchResult
from agentos.plugins.discovery import DISCOVERY_QUERIES, PluginDiscoveryService

REGISTRY = [{"name": "superpowers", "reference": "obra/superpowers", "description": "Skills de processo"}]


class FakePluginService:
    def __init__(self, entries):
        self._entries = entries
        self.search_calls = 0

    def search(self, query):
        self.search_calls += 1
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


def test_results_are_cached_until_refresh():
    plugin_service = FakePluginService(REGISTRY)
    discovery = PluginDiscoveryService(plugin_service, search_client=None)
    discovery.entries()
    discovery.entries()
    assert plugin_service.search_calls == 1
    discovery.entries(refresh=True)
    assert plugin_service.search_calls == 2
