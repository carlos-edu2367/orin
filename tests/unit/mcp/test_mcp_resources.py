"""W5: MCP resources (ListMcpResources, ReadMcpResource, ReadMcpResourceDir) and RefreshMcpTools."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.mcp.client import McpClient
from agentos.mcp.models import McpServerConfig, McpServerState, McpToolDescriptor, McpTransport
from agentos.mcp.toolset import McpToolProvider

FETCH = {"name": "fetch", "description": "f", "inputSchema": {"type": "object"}}


class _Server:
    """A fake transport that answers like a small MCP server."""

    kind = "fake"

    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = [{"name": "search", "description": "d", "inputSchema": {"type": "object"}}]
        self.on_message = None
        self.announce_change = False

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def send(self, frame):
        if "id" not in frame:
            return None
        method, params = frame["method"], frame.get("params") or {}
        if method == "initialize":
            result: dict[str, Any] = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "s"}, "capabilities": {}}
        elif method == "tools/list":
            result = {"tools": list(self.tools)}
        elif method == "resources/list":
            result = {"resources": [
                {"uri": "file:///docs/a.md", "name": "a", "mimeType": "text/markdown"},
                {"uri": "file:///docs/img.png", "name": "img"},
                {"uri": "db://x", "name": "x"},
            ]}
        elif method == "resources/read":
            uri = params["uri"]
            if uri.endswith(".png"):
                result = {"contents": [{"uri": uri, "mimeType": "image/png", "blob": "iVBORw0KGgo="}]}
            elif uri == "file:///docs/a.md":
                result = {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": "# A"}]}
            else:
                return {"jsonrpc": "2.0", "id": frame["id"], "error": {"code": -32002, "message": "Resource not found"}}
        elif method == "tools/call":
            if self.announce_change and self.on_message is not None:
                self.on_message({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
            result = {"content": [{"type": "text", "text": "ok"}]}
        else:
            return {"jsonrpc": "2.0", "id": frame["id"], "error": {"code": -32601, "message": "no"}}
        return {"jsonrpc": "2.0", "id": frame["id"], "result": result}


def _provider(server: _Server, origin: str = "user") -> McpToolProvider:
    config = McpServerConfig(
        server_id=f"{origin}:docs", user_id="u1", slug="docs", display_name="Docs", transport=McpTransport.HTTP,
        url="https://mcp.example.com/mcp", state=McpServerState.ACTIVE, origin=origin,
    )
    tools = (McpToolDescriptor("search", "d", {"type": "object"}),)
    return McpToolProvider([(config, tools, {})], client_factory=lambda config, secrets, **_: McpClient(server))


def _toolset(tmp_path: Path, provider: McpToolProvider) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"), mcp_provider=provider, public_tool_names=True)


def _names(provider: McpToolProvider) -> set[str]:
    return {item.name for item in provider.definitions()}


def test_resources_are_listed_and_read(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path, _provider(_Server()))
    listed = toolset.invoke("ListMcpResources", {})
    assert listed.status == "succeeded" and "file:///docs/a.md" in listed.content and '"server": "docs"' in listed.content
    read = toolset.invoke("ReadMcpResource", {"server": "docs", "uri": "file:///docs/a.md"})
    assert read.status == "succeeded" and read.content == "# A"
    image = toolset.invoke("ReadMcpResource", {"server": "Docs", "uri": "file:///docs/img.png"})
    assert image.images and image.images[0]["media_type"] == "image/png"
    missing = toolset.invoke("ReadMcpResource", {"server": "docs", "uri": "db://nope"})
    assert missing.status == "failed" and "Resource not found" in missing.content
    folder = toolset.invoke("ReadMcpResourceDir", {"server": "docs", "uri": "file:///docs/"})
    assert "a.md" in folder.content and "db://x" not in folder.content
    assert toolset.invoke("ListMcpResources", {"server": "nope"}).status == "failed"


def test_the_resource_tools_need_a_connected_server(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"), public_tool_names=True)
    assert "list_mcp_resources" not in {item.name for item in toolset.definitions()}


def test_refresh_replaces_a_file_servers_tools_but_not_orins_own(tmp_path: Path) -> None:
    server = _Server()
    provider = _provider(server)
    server.tools.append(FETCH)
    refreshed = _toolset(tmp_path, provider).invoke("RefreshMcpTools", {})
    assert refreshed.status == "succeeded" and '"fetch"' in refreshed.content
    assert _names(provider) == {"mcp__docs__search", "mcp__docs__fetch"}
    own_server = _Server()
    own = _provider(own_server, origin="orin")
    own_server.tools.append(FETCH)
    report = own.refresh_tools(None)
    assert report[0]["applied"] is False and report[0]["added"] == ["fetch"]
    assert _names(own) == {"mcp__docs__search"}


def test_a_list_changed_notice_during_a_call_refreshes_a_file_servers_tools() -> None:
    server = _Server()
    provider = _provider(server)
    server.announce_change = True
    server.tools.append(FETCH)
    search = next(item for item in provider.definitions() if item.name == "mcp__docs__search")
    assert search.handler().status == "succeeded"
    assert "mcp__docs__fetch" in _names(provider)
