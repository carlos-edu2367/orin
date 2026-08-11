from __future__ import annotations

import httpx
import json

from agentos.providers.http import OpenAIHTTPAdapter, ProviderHTTPSettings
from agentos.providers.models import GenerationSucceeded, ProviderRef, ReadProviderStream, StreamCompleted
from tests.unit.providers.test_provider_api import request


def test_openai_adapter_uses_real_http_contract_without_exposing_key() -> None:
    captured: dict[str, object] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured["url"] = str(http_request.url)
        captured["authorization"] = http_request.headers.get("authorization")
        captured["payload"] = json.loads(http_request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "safe answer"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    adapter = OpenAIHTTPAdapter(
        ProviderHTTPSettings(ProviderRef("provider:1"), "https://provider.invalid/v1", "secret-test-key", "gpt-test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    outcome = adapter.generate(request())

    assert isinstance(outcome, GenerationSucceeded)
    assert captured["url"] == "https://provider.invalid/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret-test-key"
    assert captured["payload"] == {"model": "gpt-test", "messages": [], "max_tokens": 20}
    assert "secret-test-key" not in repr(adapter)


def test_openai_adapter_normalizes_a_real_sse_response_to_ordered_events() -> None:
    body = 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n'
    adapter = OpenAIHTTPAdapter(
        ProviderHTTPSettings(ProviderRef("provider:1"), "https://provider.invalid/v1", "secret-test-key", "gpt-test"),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=body))),
    )
    invocation = request()

    stream = adapter.open_stream(invocation)
    events = adapter.read_stream(ReadProviderStream(invocation.context, stream.stream_id, 0, 10, invocation.limits.timeout))

    assert [event.sequence for event in events] == [1, 2, 3]
    assert events[1].delta == "hello"
    assert isinstance(events[-1], StreamCompleted)
