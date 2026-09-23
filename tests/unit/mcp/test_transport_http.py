import httpx
import pytest

from agentos.mcp.token_source import McpReauthRequired
from agentos.mcp.transport_http import HttpTransport, HttpTransportRefused, HttpUnauthorized


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


def test_a_dns_rebind_after_construction_is_refused_on_the_next_call(monkeypatch):
    # Construction sees a public address (the check passes); the record then
    # "rebinds" to a loopback address before the request is sent. Only a
    # per-request re-check catches this — a one-time check at construction
    # would happily reuse the already-validated URL forever.
    calls: list[str] = []

    def flaky_public_url(url: str, resolve_dns: bool = False) -> str:
        calls.append(url)
        if len(calls) == 1:
            return url
        raise RuntimeError("Private network addresses cannot be fetched.")

    monkeypatch.setattr("agentos.mcp.transport_http._public_url", flaky_public_url)
    transport = HttpTransport(url="https://mcp.example.com/v1", headers={})
    with pytest.raises(HttpTransportRefused):
        transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert len(calls) == 2


def test_an_sse_response_body_is_decoded_to_the_first_data_frame(monkeypatch):
    monkeypatch.setattr("agentos.mcp.transport_http._public_url", lambda url, resolve_dns=False: url)
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    transport = HttpTransport(url="https://mcp.example.com/v1", headers={},
                              client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})["result"] == {"ok": True}


class FakeTokenSource:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.forced: list[str] = []
        self.invalidated: list[str] = []

    def current(self) -> str:
        return self.tokens[0]

    def force_refresh(self, rejected: str) -> str:
        self.forced.append(rejected)
        self.tokens.pop(0)
        return self.tokens[0]

    def invalidate(self, reason: str):
        self.invalidated.append(reason)
        raise McpReauthRequired("expired")


def _ok(request_id: int = 1) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "result": {}})


def _transport(handler, **kwargs) -> HttpTransport:
    return HttpTransport(url="https://mcp.example.com/mcp", client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def test_a_401_without_token_source_exposes_the_challenge(monkeypatch):
    monkeypatch.setattr("agentos.mcp.transport_http._public_url", lambda url, resolve_dns=False: url)
    transport = _transport(lambda r: httpx.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="x"'}))
    with pytest.raises(HttpUnauthorized) as raised:
        transport.send({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert raised.value.www_authenticate == 'Bearer resource_metadata="x"'


def test_the_token_source_sets_the_bearer(monkeypatch):
    monkeypatch.setattr("agentos.mcp.transport_http._public_url", lambda url, resolve_dns=False: url)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return _ok()

    _transport(handler, token_source=FakeTokenSource(["at-1"])).send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert seen == ["Bearer at-1"]


def test_a_401_refreshes_once_and_retries(monkeypatch):
    monkeypatch.setattr("agentos.mcp.transport_http._public_url", lambda url, resolve_dns=False: url)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(401) if len(seen) == 1 else _ok()

    source = FakeTokenSource(["at-1", "at-2"])
    _transport(handler, token_source=source).send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert seen == ["Bearer at-1", "Bearer at-2"]
    assert source.forced == ["at-1"]


def test_a_second_401_invalidates_the_sign_in(monkeypatch):
    monkeypatch.setattr("agentos.mcp.transport_http._public_url", lambda url, resolve_dns=False: url)
    source = FakeTokenSource(["at-1", "at-2"])
    with pytest.raises(McpReauthRequired):
        _transport(lambda r: httpx.Response(401), token_source=source).send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert len(source.invalidated) == 1
