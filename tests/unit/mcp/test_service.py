import pytest
from sqlalchemy import create_engine, select

from agentos.mcp.models import McpServerState, McpToolDescriptor, McpTransport
from agentos.mcp.service import McpAuthorizationRequired, McpConnectionFailed, McpServerService, McpServiceError
from agentos.mcp.transport_http import HttpUnauthorized
from agentos.oauth.flow import OAuthTokens
from agentos.oauth.token_store import OAuthTokenStore
from agentos.persistence.postgres.schema import mcp_oauth_clients, metadata, oauth_pending_authorizations, oauth_tokens


@pytest.fixture()
def service(monkeypatch):
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return McpServerService(engine)


def _proposal(**overrides):
    return {"user_id": "u1", "display_name": "GitHub", "transport": "stdio", "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "secret_names": ["GITHUB_PERSONAL_ACCESS_TOKEN"], **overrides}


def test_proposing_a_server_creates_it_pending_approval(service):
    record = service.propose(_proposal())
    assert record["state"] == McpServerState.PENDING_APPROVAL.value
    assert record["slug"] == "github"
    assert record["secret_names"] == ["GITHUB_PERSONAL_ACCESS_TOKEN"]


def test_a_proposal_never_carries_secret_values(service):
    with pytest.raises(McpServiceError):
        service.propose(_proposal(secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_real"}))


def test_a_duplicate_slug_is_refused(service):
    service.propose(_proposal())
    with pytest.raises(McpServiceError):
        service.propose(_proposal())


def test_approving_stores_the_secrets_encrypted_and_activates(service):
    record = service.propose(_proposal())
    discovered = (McpToolDescriptor(name="search", description="d", input_schema={"type": "object"}),)
    activated = service.approve(user_id="u1", server_id=record["server_id"],
                                secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_real"},
                                connect=lambda config, secrets: ("2025-06-18", discovered))
    assert activated["state"] == McpServerState.ACTIVE.value
    assert activated["tool_count"] == 1
    assert "ghp_real" not in str(activated)


def test_a_failed_connection_keeps_the_server_pending(service):
    record = service.propose(_proposal())

    def failing(config, secrets):
        raise RuntimeError("token rejected")

    with pytest.raises(McpServiceError):
        service.approve(user_id="u1", server_id=record["server_id"],
                        secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "bad"}, connect=failing)
    assert service.get("u1", record["server_id"])["state"] == McpServerState.PENDING_APPROVAL.value


def test_active_servers_expose_their_cached_tools(service):
    record = service.propose(_proposal())
    service.approve(user_id="u1", server_id=record["server_id"], secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "t"},
                    connect=lambda config, secrets: ("2025-06-18", (McpToolDescriptor("search", "d", {"type": "object"}),)))
    active = service.active_servers("u1")
    assert len(active) == 1
    config, tools, secrets = active[0]
    assert config.transport is McpTransport.STDIO
    assert [item.name for item in tools] == ["search"]
    assert secrets["GITHUB_PERSONAL_ACCESS_TOKEN"] == "t"


def test_get_includes_the_cached_tools_with_their_enabled_state(service):
    record = service.propose(_proposal())
    discovered = (McpToolDescriptor(name="search", description="d", input_schema={"type": "object"}),)
    service.approve(user_id="u1", server_id=record["server_id"], secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "t"},
                    connect=lambda config, secrets: ("2025-06-18", discovered))
    detail = service.get("u1", record["server_id"])
    assert detail["tools"] == [{"name": "search", "description": "d", "enabled": True}]


def test_set_tool_enabled_is_reflected_in_get(service):
    record = service.propose(_proposal())
    discovered = (McpToolDescriptor(name="search", description="d", input_schema={"type": "object"}),)
    service.approve(user_id="u1", server_id=record["server_id"], secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "t"},
                    connect=lambda config, secrets: ("2025-06-18", discovered))
    service.set_tool_enabled("u1", record["server_id"], "search", enabled=False)
    detail = service.get("u1", record["server_id"])
    assert detail["tools"] == [{"name": "search", "description": "d", "enabled": False}]


def test_disabling_removes_the_server_from_the_active_set(service):
    record = service.propose(_proposal())
    service.approve(user_id="u1", server_id=record["server_id"], secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "t"},
                    connect=lambda config, secrets: ("2025-06-18", ()))
    service.set_enabled("u1", record["server_id"], enabled=False)
    assert service.active_servers("u1") == []


def test_a_proposal_records_its_auth_kind(service):
    with_secret = service.propose(_proposal())
    without = service.propose(_proposal(display_name="Auryly", transport="http", command=None, args=[],
                                        url="https://mcp.example.com/mcp", secret_names=[]))
    assert with_secret["auth_kind"] == "static"
    assert without["auth_kind"] == "none"


def _remote(service):
    return service.propose(_proposal(display_name="Auryly", transport="http", command=None, args=[],
                                     url="https://mcp.example.com/mcp", secret_names=[]))


def _unauthorized(config, secrets):
    raise HttpUnauthorized('Bearer resource_metadata="x"')


def test_a_401_without_credentials_asks_for_sign_in(service):
    record = _remote(service)
    with pytest.raises(McpAuthorizationRequired):
        service.approve(user_id="u1", server_id=record["server_id"], secrets={}, connect=_unauthorized)
    after = service.get("u1", record["server_id"])
    assert after["auth_kind"] == "oauth"
    assert after["state"] == McpServerState.PENDING_APPROVAL.value


def test_a_401_with_a_pasted_credential_is_a_plain_failure(service):
    record = service.propose(_proposal(transport="http", command=None, args=[], url="https://mcp.example.com/mcp",
                                       secret_names=["token"]))
    with pytest.raises(McpConnectionFailed):
        service.approve(user_id="u1", server_id=record["server_id"], secrets={"token": "bad"}, connect=_unauthorized)
    assert service.get("u1", record["server_id"])["auth_kind"] == "static"


def test_activation_after_sign_in_stores_tools_and_activates(service):
    record = _remote(service)
    tools = (McpToolDescriptor(name="list_tracks", description="d", input_schema={"type": "object"}),)
    activated = service.activate_after_authorization("u1", record["server_id"], lambda config, secrets: ("2025-06-18", tools))
    assert activated["state"] == McpServerState.ACTIVE.value
    assert activated["auth_kind"] == "oauth"
    assert activated["tool_count"] == 1


def test_a_failed_activation_keeps_the_state_and_records_why(service):
    record = _remote(service)

    def broken(config, secrets):
        raise RuntimeError("tools/list failed")

    with pytest.raises(McpConnectionFailed):
        service.activate_after_authorization("u1", record["server_id"], broken)
    after = service.get("u1", record["server_id"])
    assert after["state"] == McpServerState.PENDING_APPROVAL.value
    assert "tools/list failed" in after["state_reason"]


def test_removing_a_server_removes_its_oauth_rows(service):
    record = _remote(service)
    server_id = record["server_id"]
    OAuthTokenStore(service.engine).save(user_id="u1", provider_id=server_id, tokens=OAuthTokens("at", "rt", 60, None))
    from agentos.mcp.oauth_records import McpOAuthClient, McpOAuthRecords
    from datetime import timedelta
    records = McpOAuthRecords(service.engine)
    records.save_client(McpOAuthClient(server_id=server_id, issuer="https://a/", authorization_endpoint="https://a/authorize",
                                       token_endpoint="https://a/token", revocation_endpoint=None, resource="https://mcp.example.com/mcp",
                                       scope=None, client_id="c", client_secret=None, token_endpoint_auth_method="none",
                                       redirect_uri="http://127.0.0.1:1/cb"))
    records.add_pending(state="s", user_id="u1", server_id=server_id, code_verifier="v", redirect_uri="http://127.0.0.1:1/cb",
                        ttl=timedelta(minutes=10))

    service.remove("u1", server_id)

    with service.engine.connect() as connection:
        for table in (mcp_oauth_clients, oauth_pending_authorizations):
            assert connection.execute(select(table)).first() is None
        assert connection.execute(select(oauth_tokens)).first() is None
