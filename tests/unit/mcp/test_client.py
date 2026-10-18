from typing import Any, Mapping

import pytest

from agentos.mcp.client import McpClient
from agentos.mcp.protocol import McpProtocolError


class FakeTransport:
    kind = "fake"

    def __init__(self, responses: dict[str, Mapping[str, Any]]) -> None:
        self.responses = responses
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def open(self) -> None:
        pass

    def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
        self.sent.append(dict(frame))
        if "id" not in frame:
            return None
        return {"jsonrpc": "2.0", "id": frame["id"], "result": self.responses[str(frame["method"])]}

    def close(self) -> None:
        self.closed = True


def _transport(**overrides: Mapping[str, Any]) -> FakeTransport:
    return FakeTransport({
        "initialize": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "demo"}, "capabilities": {"tools": {}}},
        "tools/list": {"tools": [{"name": "search", "description": "d", "inputSchema": {"type": "object"}}]},
        "tools/call": {"content": [{"type": "text", "text": "hello"}]},
        **overrides,
    })


def test_initialize_negotiates_and_sends_the_initialized_notification():
    transport = _transport()
    client = McpClient(transport)
    assert client.initialize().protocol_version == "2025-06-18"
    assert [item["method"] for item in transport.sent] == ["initialize", "notifications/initialized"]


def test_list_tools_returns_sanitized_descriptors():
    client = McpClient(_transport())
    client.initialize()
    assert [item.name for item in client.list_tools()] == ["search"]


def test_call_tool_returns_content_blocks_and_the_error_flag():
    client = McpClient(_transport())
    client.initialize()
    result = client.call_tool("search", {"q": "x"})
    assert result.is_error is False
    assert result.content == ({"type": "text", "text": "hello"},)


def test_call_tool_marks_a_server_side_tool_error():
    client = McpClient(_transport(**{"tools/call": {"content": [{"type": "text", "text": "boom"}], "isError": True}}))
    client.initialize()
    assert client.call_tool("search", {}).is_error is True


def test_calling_a_tool_before_initialize_is_a_protocol_error():
    with pytest.raises(McpProtocolError):
        McpClient(_transport()).call_tool("search", {})


def test_close_closes_the_transport():
    transport = _transport()
    client = McpClient(transport)
    client.initialize()
    client.close()
    assert transport.closed is True


def test_call_tool_synthesizes_text_from_structured_content_when_content_is_empty():
    client = McpClient(_transport(**{"tools/call": {"content": [], "structuredContent": {"funnelId": "f_123", "steps": 9}}}))
    client.initialize()
    result = client.call_tool("get_funnel", {"id": "f_123"})
    assert len(result.content) == 1
    assert result.content[0]["type"] == "text"
    assert '"funnelId": "f_123"' in result.content[0]["text"]


def test_list_tools_paginates_beyond_eight_pages():
    class PaginatedTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__({})
            self.page = 0

        def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
            self.sent.append(dict(frame))
            if "id" not in frame:
                return None
            if frame["method"] == "initialize":
                return {"jsonrpc": "2.0", "id": frame["id"], "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "demo"}, "capabilities": {"tools": {}}}}
            self.page += 1
            next_cursor = f"cursor_{self.page}" if self.page < 12 else None
            return {
                "jsonrpc": "2.0",
                "id": frame["id"],
                "result": {
                    "tools": [{"name": f"t_{self.page}", "description": "d", "inputSchema": {"type": "object"}}],
                    **({"nextCursor": next_cursor} if next_cursor else {}),
                },
            }

    client = McpClient(PaginatedTransport())
    client.initialize()
    tools = client.list_tools()
    assert len(tools) == 12
    assert [t.name for t in tools] == [f"t_{i}" for i in range(1, 13)]


def test_list_tools_stops_on_repeating_cursor_cycle():
    class CyclingTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__({})
            self.calls = 0

        def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
            self.sent.append(dict(frame))
            if "id" not in frame:
                return None
            if frame["method"] == "initialize":
                return {"jsonrpc": "2.0", "id": frame["id"], "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "demo"}, "capabilities": {"tools": {}}}}
            self.calls += 1
            return {
                "jsonrpc": "2.0",
                "id": frame["id"],
                "result": {
                    "tools": [{"name": f"t_{self.calls}", "description": "d", "inputSchema": {"type": "object"}}],
                    "nextCursor": "same_cursor",
                },
            }

    transport = CyclingTransport()
    client = McpClient(transport)
    client.initialize()
    tools = client.list_tools()
    assert len(tools) == 2
    assert transport.calls == 2


