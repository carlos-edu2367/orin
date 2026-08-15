from agentos.mcp.catalog import CATALOG, find_catalog_entry, search_catalog
from agentos.mcp.models import McpTransport


def test_every_entry_declares_what_the_user_must_provide():
    for entry in CATALOG:
        assert entry.catalog_id and entry.display_name and entry.summary
        assert entry.setup_instructions
        for secret in entry.secrets:
            assert secret.name and secret.label and secret.how_to_obtain


def test_every_stdio_entry_has_a_command_and_every_http_entry_a_url():
    for entry in CATALOG:
        if entry.transport is McpTransport.STDIO:
            assert entry.command and entry.url is None
        else:
            assert entry.url and entry.command is None


def test_search_matches_name_and_keywords():
    assert any(entry.catalog_id == "filesystem" for entry in search_catalog("arquivos"))
    assert any(entry.catalog_id == "github" for entry in search_catalog("GitHub"))


def test_find_catalog_entry_returns_none_for_an_unknown_id():
    assert find_catalog_entry("nope") is None
