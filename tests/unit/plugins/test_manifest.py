import pytest
from agentos.plugins.manifest import ManifestRejected, parse_mcp_config, parse_plugin_manifest

def test_manifest_is_bounded_and_mcp_drops_secret_values():
    assert parse_plugin_manifest({"name":"x", "version":"1.0.0", "author":{"name":"Ana"}}).author == "Ana"
    with pytest.raises(ManifestRejected): parse_plugin_manifest({"name":"x", "version":"latest"})
    servers = parse_mcp_config({"mcpServers":{"x":{"command":"npx","env":{"TOKEN":"secret"}}}})
    assert servers[0].secret_names == ("TOKEN",) and "hardcoded-secret" not in repr(servers[0])
