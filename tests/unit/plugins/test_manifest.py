import pytest
from agentos.plugins.manifest import ManifestRejected, parse_mcp_config, parse_plugin_manifest

def test_manifest_is_bounded_and_mcp_drops_secret_values():
    assert parse_plugin_manifest({"name":"x", "version":"1.0.0", "author":{"name":"Ana"}}).author == "Ana"
    with pytest.raises(ManifestRejected): parse_plugin_manifest({"name":"x", "version":"latest"})
    servers = parse_mcp_config({"mcpServers":{"x":{"command":"npx","env":{"TOKEN":"secret"}}}})
    assert servers[0].secret_names == ("TOKEN",) and "hardcoded-secret" not in repr(servers[0])


def test_manifest_reads_inline_mcp_servers():
    manifest = parse_plugin_manifest({
        "name": "obsidian-second-brain",
        "version": "0.14.0",
        "mcpServers": {"vault": {"command": "uv", "args": ["run", "server.py"]}},
    })

    assert len(manifest.mcp_servers) == 1
    assert manifest.mcp_servers[0].slug == "vault"
    assert manifest.mcp_servers[0].command == "uv"
    assert manifest.mcp_servers[0].args == ("run", "server.py")


def test_manifest_without_mcp_servers_is_empty_not_none():
    assert parse_plugin_manifest({"name": "demo", "version": "1.0.0"}).mcp_servers == ()


def test_manifest_path_fields_default_to_conventional_directories():
    manifest = parse_plugin_manifest({"name": "demo", "version": "1.0.0"})

    assert manifest.commands_path == "commands"
    assert manifest.hooks_path == "hooks"


def test_manifest_path_fields_are_normalized():
    manifest = parse_plugin_manifest({
        "name": "demo", "version": "1.0.0",
        "commands": "./commands/", "hooks": "./config/hooks",
    })

    assert manifest.commands_path == "commands"
    assert manifest.hooks_path == "config/hooks"


@pytest.mark.parametrize("value", ["../outside", "/etc", "commands/../../escape", "C:\\\\windows"])
def test_manifest_rejects_a_path_escaping_the_package(value):
    with pytest.raises(ManifestRejected):
        parse_plugin_manifest({"name": "demo", "version": "1.0.0", "commands": value})
