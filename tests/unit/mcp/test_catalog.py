from agentos.mcp.catalog import CATALOG, find_catalog_entry, search_catalog
from agentos.mcp.models import McpTransport


def test_github_entry_uses_the_hosted_endpoint_with_a_pat_no_deprecated_npx_package():
    # @modelcontextprotocol/server-github is deprecated upstream (confirmed by running
    # `npx -y @modelcontextprotocol/server-github`, which prints an npm deprecation
    # warning). GitHub's hosted remote MCP server accepts a Personal Access Token via
    # `Authorization: Bearer <PAT>` (see docs.github.com "Setting up the GitHub MCP
    # Server"), so this needs no OAuth: an HTTP entry with a `token` secret is enough —
    # toolset.py's HTTP connector already turns secrets['token'] into that header.
    entry = find_catalog_entry("github")
    assert entry is not None
    assert entry.transport is McpTransport.HTTP
    assert entry.url == "https://api.githubcopilot.com/mcp/"
    assert entry.command is None
    assert [secret.name for secret in entry.secrets] == ["token"]


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
