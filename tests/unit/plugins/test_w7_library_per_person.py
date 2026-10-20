"""W7 Batch F (W5-D1): the plugin library lists one person's marketplaces, never another person's."""
from __future__ import annotations

from agentos.plugins.discovery import PluginDiscoveryService


class _Marketplaces:
    """Each person's marketplace plugins, keyed by person; `search` must be told whose."""

    def __init__(self) -> None:
        self.by_person = {
            "ana": [{"name": "ana-tools", "reference": "https://github.com/ana/tools.git", "description": "Ana"}],
            "bia": [{"name": "bia-tools", "reference": "https://github.com/bia/tools.git", "description": "Bia"}],
        }
        self.asked: list[object] = []

    def search(self, query, *, user_id=None):
        self.asked.append(user_id)
        if user_id is None:  # the old, global behaviour this fix removes
            return [item for items in self.by_person.values() for item in items]
        return self.by_person.get(user_id, [])


def test_each_person_sees_only_their_own_marketplaces_even_from_the_cache() -> None:
    marketplaces = _Marketplaces()
    discovery = PluginDiscoveryService(marketplaces, search_client=None)
    ana, _ = discovery.entries(user_id="ana")
    bia, _ = discovery.entries(user_id="bia")
    again, _ = discovery.entries(user_id="ana")  # cached, still Ana's
    assert [entry.name for entry in ana] == ["ana-tools"]
    assert [entry.name for entry in bia] == ["bia-tools"]
    assert [entry.name for entry in again] == ["ana-tools"]
    assert marketplaces.asked == ["ana", "bia"]
    searched, _ = discovery.entries(user_id="bia", query="tools")
    assert [entry.name for entry in searched] == ["bia-tools"]
