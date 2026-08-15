from agentos.plugins.marketplace import find_plugin_entry, parse_marketplace

def test_marketplace_normalizes_sources():
    marketplace = parse_marketplace({"name":"community", "plugins":[{"name":"Demo","source":{"repo":"a/b"}}]})
    assert find_plugin_entry(marketplace, "demo").reference == "a/b"
