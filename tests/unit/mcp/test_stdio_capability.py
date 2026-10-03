import pytest
from sqlalchemy import create_engine, update

from agentos.configuration.capabilities import CapabilityUnavailable
from agentos.mcp.service import McpServerService
from agentos.persistence.postgres.schema import mcp_servers, metadata


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    return engine


def test_stdio_servers_cannot_be_proposed_without_the_capability(engine):
    service = McpServerService(engine, allow_stdio=False)
    with pytest.raises(CapabilityUnavailable):
        service.propose({"user_id": "u1", "display_name": "Files", "transport": "stdio", "command": "npx"})
    remote = service.propose({"user_id": "u1", "display_name": "Remote", "transport": "http", "url": "https://mcp.example.com/mcp"})
    assert remote["transport"] == "http"


def test_existing_stdio_servers_never_reach_a_turn_or_connect(engine):
    permissive = McpServerService(engine)
    server = permissive.propose({"user_id": "u1", "display_name": "Files", "transport": "stdio", "command": "npx"})
    with engine.begin() as connection:
        connection.execute(update(mcp_servers).values(state="active"))
    closed = McpServerService(engine, allow_stdio=False)
    assert closed.active_servers("u1") == []

    def connect(config, secrets):
        raise AssertionError("must not spawn")
    with pytest.raises(CapabilityUnavailable):
        closed.approve(user_id="u1", server_id=server["server_id"], secrets={}, connect=connect)
    with pytest.raises(CapabilityUnavailable):
        closed.test("u1", server["slug"], connect)
    with pytest.raises(CapabilityUnavailable):
        closed.activate_after_authorization("u1", server["server_id"], connect)
    assert len(permissive.active_servers("u1")) == 1
