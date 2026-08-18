from __future__ import annotations

import httpx
import pytest

from agentos.agentic.provider_stream import HTTPProviderStreamTransport, ProviderKeyRejected


def _transport(handler) -> HTTPProviderStreamTransport:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HTTPProviderStreamTransport(provider="openrouter", base_url="https://example.test", api_key="k", model="m", client=client)


def _request() -> dict[str, object]:
    return {"messages": [{"role": "user", "content": "hi"}], "tools": [], "max_output_tokens": 16}


@pytest.mark.parametrize("status", [401, 403, 429])
def test_an_auth_or_rate_limit_status_raises_provider_key_rejected(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "no"}})

    with pytest.raises(ProviderKeyRejected):
        list(_transport(handler).stream(_request()))


def test_a_server_error_status_does_not_raise_provider_key_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "no"}})

    with pytest.raises(httpx.HTTPStatusError):
        list(_transport(handler).stream(_request()))


def test_a_connection_failure_raises_provider_key_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ProviderKeyRejected):
        list(_transport(handler).stream(_request()))


def test_a_successful_response_still_streams_normally() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="data: [DONE]\n")

    assert list(_transport(handler).stream(_request())) == []
