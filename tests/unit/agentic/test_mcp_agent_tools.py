import pytest

from agentos.agentic.agent_tools import AgentToolError, AgentToolset
from agentos.agentic.workspace import ConversationWorkspace


class FakeMcpService:
    def __init__(self) -> None:
        self.proposals: list[dict] = []

    def list(self, user_id):
        return [{"server_id": "s1", "slug": "github", "display_name": "GitHub", "state": "active", "tool_count": 3}]

    def propose(self, command):
        self.proposals.append(dict(command))
        return {"server_id": "s2", "slug": command.get("slug") or "notion", "display_name": command["display_name"],
                "state": "pending_approval", "secret_names": list(command.get("secret_names") or [])}

    def test(self, user_id, slug, connect):
        assert user_id == "u1"
        assert slug == "github"
        assert callable(connect)
        return {"connected": True, "protocol_version": "2025-06-18", "tools": ["search"], "error": None}


def _toolset(tmp_path, service):
    return AgentToolset(ConversationWorkspace(root=tmp_path, conversation_id="c1"),
                        mcp_service=service, mcp_user_id="u1")


def test_the_tools_are_absent_without_a_service(tmp_path):
    names = {item.name for item in AgentToolset(ConversationWorkspace(root=tmp_path, conversation_id="c1")).definitions()}
    assert "configure_mcp" not in names


def test_the_tools_are_published_with_a_service(tmp_path):
    names = {item.name for item in _toolset(tmp_path, FakeMcpService()).definitions()}
    assert {"list_mcp_catalog", "list_mcp_servers", "configure_mcp"} <= names


def test_list_mcp_catalog_explains_the_required_secrets(tmp_path):
    result = _toolset(tmp_path, FakeMcpService()).list_mcp_catalog(query="github")
    entry = result["payload"]["entries"][0]
    assert entry["catalog_id"] == "github"
    assert entry["secrets"][0]["how_to_obtain"]


def test_configure_mcp_creates_a_pending_server_and_asks_for_approval(tmp_path):
    service = FakeMcpService()
    outcome = _toolset(tmp_path, service).configure_mcp(catalog_id="github", display_name="GitHub")
    assert outcome.payload["mcp_approval"] is True
    assert outcome.payload["wait_for_user"] is True
    assert outcome.payload["server"]["state"] == "pending_approval"
    assert service.proposals[0]["secret_names"] == ["token"]


def test_configure_mcp_refuses_a_secret_value_in_its_arguments(tmp_path):
    with pytest.raises(AgentToolError):
        _toolset(tmp_path, FakeMcpService()).configure_mcp(
            catalog_id="github", display_name="GitHub", secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"})


def test_configure_mcp_refuses_an_unknown_catalog_id_without_an_explicit_transport(tmp_path):
    with pytest.raises(AgentToolError):
        _toolset(tmp_path, FakeMcpService()).configure_mcp(catalog_id="nope", display_name="X")


def test_test_mcp_server_passes_a_real_connector_to_the_service(tmp_path):
    result = _toolset(tmp_path, FakeMcpService()).test_mcp_server(slug="github")
    assert result["payload"]["connected"] is True
    assert result["payload"]["tools"] == ["search"]


def test_test_mcp_server_uses_oauth_connector_when_provider_has_oauth(tmp_path, monkeypatch):
    from agentos.mcp.models import McpAuthKind, McpServerConfig, McpServerState, McpTransport

    seen_token_sources: list[object] = []

    def fake_discover(config, secrets, *, token_source=None):
        seen_token_sources.append(token_source)
        return "2025-06-18", ()

    monkeypatch.setattr("agentos.mcp.toolset.discover", fake_discover)

    class _FakeOAuth:
        def has_sign_in(self, config): return True
        def token_source(self, config): return "oauth-token-source"

    class _FakeProvider:
        _oauth = _FakeOAuth()
        def definitions(self): return ()
        def close(self): pass

    class _OAuthService(FakeMcpService):
        def test(self, user_id, slug, connect):
            cfg = McpServerConfig(
                server_id="s1", user_id=user_id, slug=slug, display_name="Auryly",
                transport=McpTransport.HTTP, url="https://auryly.com/mcp",
                state=McpServerState.ACTIVE, auth_kind=McpAuthKind.OAUTH,
            )
            connect(cfg, {})
            return {"connected": True, "protocol_version": "2025-06-18", "tools": ["list_tracks"], "error": None}

    toolset = AgentToolset(
        ConversationWorkspace(root=tmp_path, conversation_id="c1"),
        mcp_provider=_FakeProvider(), mcp_service=_OAuthService(), mcp_user_id="u1",
    )
    toolset.test_mcp_server(slug="auryly")
    assert seen_token_sources == ["oauth-token-source"]

