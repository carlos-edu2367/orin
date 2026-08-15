from agentos.mcp.catalog import CATALOG, find_catalog_entry, oauth_configured, search_catalog
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


def test_google_drive_entry_declares_an_oauth_requirement_no_secret_field():
    entry = find_catalog_entry("google-drive")
    assert entry is not None
    assert entry.transport is McpTransport.HTTP
    assert entry.url == "https://drivemcp.googleapis.com/mcp/v1"
    assert entry.secrets == ()
    assert entry.oauth is not None
    assert entry.oauth.scopes == ("https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/drive.file")
    assert entry.oauth.client_id_env_var == "AGENTOS_OAUTH_GOOGLE_CLIENT_ID"


def test_vercel_entry_declares_an_oauth_requirement_and_warns_client_approval_is_needed():
    entry = find_catalog_entry("vercel")
    assert entry is not None
    assert entry.transport is McpTransport.HTTP
    assert entry.url == "https://mcp.vercel.com"
    assert entry.oauth is not None
    assert entry.oauth.client_id_env_var == "AGENTOS_OAUTH_VERCEL_CLIENT_ID"
    # Vercel MCP only accepts clients it has personally reviewed and approved —
    # a self-registered client_id is not sufficient the way it is for Google.
    assert "aprovad" in entry.setup_instructions.lower() or "approv" in entry.setup_instructions.lower()


def test_oauth_configured_is_false_without_a_client_id_and_true_once_set(monkeypatch):
    entry = find_catalog_entry("google-drive")
    monkeypatch.delenv("AGENTOS_OAUTH_GOOGLE_CLIENT_ID", raising=False)
    assert oauth_configured(entry) is False
    monkeypatch.setenv("AGENTOS_OAUTH_GOOGLE_CLIENT_ID", "some-client-id")
    assert oauth_configured(entry) is True


def test_oauth_configured_is_true_for_an_entry_with_no_oauth_requirement():
    assert oauth_configured(find_catalog_entry("github")) is True
