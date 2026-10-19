"""W5: what an MCP server sends besides answers — its requests are answered, its notifications noted."""
from __future__ import annotations

import json
import sys
from typing import Any, Mapping

import httpx

from agentos.mcp.client import McpClient
from agentos.mcp.protocol import METHOD_NOT_FOUND, dispatch_server_message
from agentos.mcp.transport_http import HttpTransport
from agentos.mcp.transport_stdio import StdioTransport


class _Transport:
    kind = "fake"

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.on_message = None

    def open(self) -> None:
        pass

    def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
        self.sent.append(dict(frame))
        if "id" not in frame:
            return None
        return {"jsonrpc": "2.0", "id": frame["id"], "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "s"}, "capabilities": {}}}

    def close(self) -> None:
        pass


def _request(method: str, params: dict | None = None, request_id: object = "s1") -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, **({"params": params} if params is not None else {})}


def test_the_client_answers_the_servers_requests() -> None:
    asked: list[dict] = []
    transport = _Transport()
    client = McpClient(transport, roots=(("file:///C:/work", "work"),), elicit=lambda params: asked.append(params) or {"action": "accept", "content": {"name": "x"}})
    client.initialize()
    assert transport.sent[0]["params"]["capabilities"] == {"roots": {"listChanged": False}, "elicitation": {}}
    handle = transport.on_message
    assert dispatch_server_message(_request("ping"), handle) == {"jsonrpc": "2.0", "id": "s1", "result": {}}
    assert dispatch_server_message(_request("roots/list"), handle)["result"] == {"roots": [{"uri": "file:///C:/work", "name": "work"}]}
    answer = dispatch_server_message(_request("elicitation/create", {"message": "Name?", "requestedSchema": {"type": "object"}}), handle)
    assert answer["result"] == {"action": "accept", "content": {"name": "x"}} and asked[0]["message"] == "Name?"
    refused = dispatch_server_message(_request("sampling/createMessage", {}), handle)
    assert refused["error"]["code"] == METHOD_NOT_FOUND


def test_without_a_handler_an_elicitation_is_declined() -> None:
    transport = _Transport()
    McpClient(transport)
    assert dispatch_server_message(_request("elicitation/create", {"message": "?"}), transport.on_message)["result"] == {"action": "decline"}


def test_list_changed_notifications_are_noted() -> None:
    seen: list[str] = []
    transport = _Transport()
    client = McpClient(transport, on_notification=lambda method, params: seen.append(method))
    for method in ("notifications/tools/list_changed", "notifications/resources/list_changed", "notifications/message"):
        assert dispatch_server_message({"jsonrpc": "2.0", "method": method}, transport.on_message) is None
    assert client.changed == {"tools", "resources"} and seen[-1] == "notifications/message"


def test_a_handler_that_fails_answers_with_an_error() -> None:
    def broken(_message):
        raise RuntimeError("boom")

    assert dispatch_server_message(_request("ping"), broken)["error"]["code"] == -32603
    assert dispatch_server_message({"jsonrpc": "2.0", "method": "notifications/x"}, broken) is None


CHATTY_SERVER = """
import json, sys
print("Server starting...", flush=True)  # not JSON: skipped
for line in sys.stdin:
    frame = json.loads(line)
    if "id" not in frame or "method" not in frame:
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}) + "\\n")
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": "srv-1", "method": "ping"}) + "\\n")
    sys.stdout.flush()
    reply = json.loads(sys.stdin.readline())
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": frame["id"], "result": {"pong_seen": reply.get("id") == "srv-1" and reply.get("result") == {}}}) + "\\n")
    sys.stdout.flush()
"""


def test_stdio_answers_a_server_request_in_the_middle_of_a_call(tmp_path) -> None:
    script = tmp_path / "chatty.py"
    script.write_text(CHATTY_SERVER, encoding="utf-8")
    transport = StdioTransport(command=sys.executable, args=(str(script),), env={}, allow_any_command=True, timeout=10)
    notes: list[str] = []
    transport.on_message = lambda message: notes.append(message["method"]) or ({} if message.get("id") is not None else None)
    transport.open()
    try:
        answer = transport.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
    finally:
        transport.close()
    assert answer["result"] == {"pong_seen": True} and notes == ["notifications/tools/list_changed", "ping"]


def test_streamable_http_answers_a_server_request_inside_the_event_stream(monkeypatch) -> None:
    monkeypatch.setattr("agentos.mcp.transport_http._public_url", lambda url, resolve_dns=False: url)
    replies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        frame = json.loads(request.content)
        if "method" not in frame:  # our answer to the server's request
            replies.append(frame)
            return httpx.Response(202)
        events = (
            'data: {"jsonrpc":"2.0","id":"e1","method":"elicitation/create","params":{"message":"?"}}\n\n'
            'data: {"jsonrpc":"2.0","id":3,"result":{"done":true}}\n\n'
        )
        return httpx.Response(200, text=events, headers={"content-type": "text/event-stream"})

    transport = HttpTransport(url="https://mcp.example.com/mcp", headers={}, client=httpx.Client(transport=httpx.MockTransport(handler)))
    transport.on_message = lambda message: {"action": "decline"} if message.get("method") == "elicitation/create" else None
    assert transport.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call"})["result"] == {"done": True}
    assert replies == [{"jsonrpc": "2.0", "id": "e1", "result": {"action": "decline"}}]
