"""End-to-end coverage of the approval-to-invocation MCP path.

Uses a real stdio subprocess (tests/fixtures/mcp_echo_server.py) and the real
McpServerService/McpToolProvider/AgentToolset wiring, over an in-memory
SQLite engine. No fakes: if this test passes, the whole path works.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.mcp.models import McpServerState
from agentos.mcp.service import McpServerService
from agentos.mcp.toolset import McpToolProvider, discover
from agentos.persistence.postgres.schema import metadata

FIXTURE_SERVER = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_echo_server.py"


@pytest.fixture()
def service(monkeypatch):
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return McpServerService(engine)


def test_a_real_stdio_server_is_proposed_approved_and_invoked(service, tmp_path):
    record = service.propose({
        "user_id": "u1", "display_name": "Echo", "transport": "stdio",
        "command": sys.executable, "args": [str(FIXTURE_SERVER)], "secret_names": [],
    })
    assert record["state"] == McpServerState.PENDING_APPROVAL.value

    activated = service.approve(user_id="u1", server_id=record["server_id"], secrets={}, connect=discover)
    assert activated["state"] == McpServerState.ACTIVE.value
    assert activated["tool_count"] == 1
    assert activated["tools"] == [{"name": "echo", "description": "Echoes the given text back.", "enabled": True}]

    bundles = service.active_servers("u1")
    assert len(bundles) == 1

    provider = McpToolProvider(bundles)
    definitions = provider.definitions()
    assert [item.name for item in definitions] == ["mcp__echo__echo"]

    toolset = AgentToolset(ConversationWorkspace(root=tmp_path, conversation_id="c1"), mcp_provider=provider)
    outcome = toolset.invoke("mcp__echo__echo", {"text": "hello from the integration test"})
    assert outcome.status == "succeeded"
    assert outcome.content == "hello from the integration test"

    assert provider.open_session_count == 1
    toolset.close()
    assert provider.open_session_count == 0
