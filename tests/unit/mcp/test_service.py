import pytest
from sqlalchemy import create_engine

from agentos.mcp.models import McpServerState, McpToolDescriptor, McpTransport
from agentos.mcp.service import McpServerService, McpServiceError
from agentos.persistence.postgres.schema import metadata


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


def test_disabling_removes_the_server_from_the_active_set(service):
    record = service.propose(_proposal())
    service.approve(user_id="u1", server_id=record["server_id"], secrets={"GITHUB_PERSONAL_ACCESS_TOKEN": "t"},
                    connect=lambda config, secrets: ("2025-06-18", ()))
    service.set_enabled("u1", record["server_id"], enabled=False)
    assert service.active_servers("u1") == []
