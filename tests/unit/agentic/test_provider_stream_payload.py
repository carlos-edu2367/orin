from __future__ import annotations

import httpx
import pytest

from agentos.agentic.provider_stream import HTTPProviderStreamTransport


def _transport(provider: str, captured: list[dict]) -> HTTPProviderStreamTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.append(json.loads(request.content))
        return httpx.Response(200, text="data: [DONE]\n")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HTTPProviderStreamTransport(provider=provider, base_url="https://example.test", api_key="k", model="m", client=client)


def _request() -> dict[str, object]:
    return {
        "messages": [{"role": "system", "content": "you are orin"}, {"role": "user", "content": "hi"}],
        "tools": [
            {"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "write_file", "description": "write", "parameters": {"type": "object", "properties": {}}}},
        ],
        "max_output_tokens": 512,
    }


def test_anthropic_payload_marks_the_cacheable_prefix() -> None:
    captured: list[dict] = []
    list(_transport("anthropic", captured).stream(_request()))

    payload = captured[0]
    assert payload["system"] == [{"type": "text", "text": "you are orin", "cache_control": {"type": "ephemeral"}}]
    assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in payload["tools"][0]


def test_openai_payload_asks_for_usage_in_the_stream() -> None:
    captured: list[dict] = []
    list(_transport("openrouter", captured).stream(_request()))

    assert captured[0]["stream_options"] == {"include_usage": True}


def test_tool_choice_is_forwarded_when_present() -> None:
    captured: list[dict] = []
    request = {**_request(), "tool_choice": "none"}
    list(_transport("openrouter", captured).stream(request))

    assert captured[0]["tool_choice"] == "none"


def test_anthropic_tool_choice_uses_its_own_shape() -> None:
    captured: list[dict] = []
    request = {**_request(), "tool_choice": "none"}
    list(_transport("anthropic", captured).stream(request))

    assert captured[0]["tool_choice"] == {"type": "none"}
