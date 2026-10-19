"""W5: the legacy HTTP+SSE and the WebSocket transports, for MCP servers in configuration files."""
from __future__ import annotations

import json
import queue
import threading

import httpx
import pytest
from sqlalchemy import create_engine

from agentos.mcp.file_config import parse_server
from agentos.mcp.file_servers import file_bundle
from agentos.mcp.models import McpTransport
from agentos.mcp.service import McpServerService, McpServiceError
from agentos.mcp.toolset import build_client
from agentos.mcp.transport_sse import SseTransport, SseTransportError
from agentos.mcp.transport_ws import WebSocketTransport, WebSocketTransportError
from agentos.persistence.postgres.schema import metadata


def _sse_client(events: "queue.Queue[bytes | None]", on_post):
    def stream():
        while True:
            item = events.get(timeout=5)
            if item is None:
                return
            yield item

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream())
        on_post(str(request.url), json.loads(request.content))
        return httpx.Response(202)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sse_posts_to_the_named_endpoint_and_reads_answers_from_the_stream() -> None:
    events: queue.Queue[bytes | None] = queue.Queue()
    events.put(b"event: endpoint\ndata: /messages?session=abc\n\n")
    posted: list[tuple[str, dict]] = []

    def on_post(url: str, frame: dict) -> None:
        posted.append((url, frame))
        if frame.get("method") == "tools/call":
            events.put(b'event: message\ndata: {"jsonrpc":"2.0","id":"s9","method":"ping"}\n\n')
        elif "result" in frame:  # our answer to the server's ping
            events.put(b'event: message\ndata: {"jsonrpc":"2.0","id":4,"result":{"ok":true}}\n\n')

    transport = SseTransport(url="http://127.0.0.1:9/sse", client=_sse_client(events, on_post), allow_local=True, timeout=5)
    transport.on_message = lambda message: {} if message.get("method") == "ping" else None
    transport.open()
    try:
        assert transport.send({"jsonrpc": "2.0", "id": 4, "method": "tools/call"})["result"] == {"ok": True}
    finally:
        events.put(None)
        transport.close()
    assert posted[0][0] == "http://127.0.0.1:9/messages?session=abc"
    assert posted[1][1] == {"jsonrpc": "2.0", "id": "s9", "result": {}}


def test_sse_refuses_an_endpoint_on_another_origin() -> None:
    events: queue.Queue[bytes | None] = queue.Queue()
    events.put(b"event: endpoint\ndata: https://elsewhere.example.com/messages\n\n")
    transport = SseTransport(url="http://127.0.0.1:9/sse", client=_sse_client(events, lambda *_: None), allow_local=True, timeout=5)
    with pytest.raises(SseTransportError, match="another origin"):
        transport.open()
    events.put(None)


def test_without_allow_local_the_addresses_must_be_public_and_secure() -> None:
    with pytest.raises(SseTransportError):
        SseTransport(url="http://127.0.0.1:9/sse")
    with pytest.raises(WebSocketTransportError):
        WebSocketTransport(url="ws://127.0.0.1:9/mcp")


def test_websocket_round_trips_and_answers_the_servers_requests() -> None:
    from websockets.sync.server import serve

    def handler(connection) -> None:
        for raw in connection:
            frame = json.loads(raw)
            if frame.get("method") != "tools/call":
                continue
            connection.send(json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}))
            connection.send(json.dumps({"jsonrpc": "2.0", "id": "w1", "method": "ping"}))
            reply = json.loads(connection.recv())
            connection.send(json.dumps({"jsonrpc": "2.0", "id": frame["id"], "result": {"pong": reply.get("result") == {}, "protocol": connection.subprotocol}}))

    with serve(handler, "127.0.0.1", 0, subprotocols=["mcp"]) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.socket.getsockname()[1]
        transport = WebSocketTransport(url=f"ws://127.0.0.1:{port}/mcp", allow_local=True, timeout=5)
        seen: list[str] = []
        transport.on_message = lambda message: seen.append(message["method"]) or ({} if message.get("id") is not None else None)
        transport.open()
        try:
            answer = transport.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
        finally:
            transport.close()
            server.shutdown()
    assert answer["result"] == {"pong": True, "protocol": "mcp"}
    assert seen == ["notifications/tools/list_changed", "ping"]


def test_file_servers_may_use_sse_and_websocket() -> None:
    sse = parse_server("live", {"type": "sse", "url": "http://127.0.0.1:9/sse", "headers": {"K": "v"}}, scope="user", source="t", environ={})
    config, _, secrets = file_bundle(sse, user_id="u1")
    assert config.transport is McpTransport.SSE
    client = build_client(config, secrets)
    assert isinstance(client._transport, SseTransport) and client._transport._headers == {"K": "v"}
    sock = parse_server("sock", {"type": "ws", "url": "ws://127.0.0.1:9/mcp"}, scope="user", source="t", environ={})
    config, _, secrets = file_bundle(sock, user_id="u1")
    assert config.transport is McpTransport.WEBSOCKET and isinstance(build_client(config, secrets)._transport, WebSocketTransport)


def test_the_model_cannot_propose_an_sse_or_websocket_server(monkeypatch) -> None:
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    service = McpServerService(engine)
    for transport, url in (("sse", "https://mcp.example.com/sse"), ("ws", "wss://mcp.example.com/mcp")):
        with pytest.raises(McpServiceError):
            service.propose({"user_id": "u1", "display_name": f"x-{transport}", "transport": transport, "url": url})

def test_the_address_is_checked_again_when_the_connection_opens(monkeypatch) -> None:
    calls: list[str] = []

    def rebinding(url, resolve_dns=False):
        calls.append(url)
        if len(calls) > 1:
            raise RuntimeError("Private network addresses cannot be fetched.")
        return url

    monkeypatch.setattr("agentos.mcp.transport_sse._public_url", rebinding)
    requested: list[object] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: requested.append(request) or httpx.Response(500)))
    with pytest.raises(SseTransportError, match="Private network"):
        SseTransport(url="https://mcp.example.com/sse", client=client).open()
    assert requested == []  # refused before any request
    calls.clear()
    attempts: list[object] = []

    def connect(*args, **kwargs):
        attempts.append(args)
        raise OSError("unreachable")

    with pytest.raises(WebSocketTransportError, match="Private network"):
        WebSocketTransport(url="wss://mcp.example.com/mcp", connect=connect).open()
    assert attempts == []
