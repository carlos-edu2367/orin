import httpx
import pytest

from agentos.mcp.transport_http import HttpTransport, HttpTransportRefused


def test_a_loopback_url_is_refused():
    with pytest.raises(HttpTransportRefused):
        HttpTransport(url="http://127.0.0.1:9000/mcp", headers={})


def test_a_plain_http_public_url_is_refused():
    with pytest.raises(HttpTransportRefused):
        HttpTransport(url="http://mcp.example.com/v1", headers={})


def test_the_transport_posts_a_frame_and_returns_the_json_response(monkeypatch):
    # DNS resolution for a fictitious host is not reliable in a sandboxed test
    # environment; the two refusal tests above already exercise the real
    # network-policy function, so here it is bypassed deliberately.
    monkeypatch.setattr("agentos.mcp.transport_http._public_url", lambda url, resolve_dns=False: url)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/json, text/event-stream"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
                              headers={"Mcp-Session-Id": "abc"})

    transport = HttpTransport(url="https://mcp.example.com/v1", headers={},
                              client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})["result"] == {"ok": True}
    assert transport.session_id == "abc"


def test_an_sse_response_body_is_decoded_to_the_first_data_frame(monkeypatch):
    monkeypatch.setattr("agentos.mcp.transport_http._public_url", lambda url, resolve_dns=False: url)
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    transport = HttpTransport(url="https://mcp.example.com/v1", headers={},
                              client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})["result"] == {"ok": True}
