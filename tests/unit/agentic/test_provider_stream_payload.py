from __future__ import annotations

import httpx
import pytest

from agentos.agentic.provider_stream import ANTHROPIC_REQUIRED_MAX_TOKENS, HTTPProviderStreamTransport


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


def test_openai_payload_omits_the_cap_when_no_output_limit_is_configured() -> None:
    captured: list[dict] = []
    request = {key: value for key, value in _request().items() if key != "max_output_tokens"}

    list(_transport("openrouter", captured).stream(request))

    assert "max_tokens" not in captured[0]


def test_anthropic_payload_keeps_the_required_max_tokens_without_a_configured_limit() -> None:
    captured: list[dict] = []
    request = {key: value for key, value in _request().items() if key != "max_output_tokens"}

    list(_transport("anthropic", captured).stream(request))

    assert captured[0]["max_tokens"] == ANTHROPIC_REQUIRED_MAX_TOKENS


def test_anthropic_payload_marks_the_cacheable_prefix() -> None:
    captured: list[dict] = []
    list(_transport("anthropic", captured).stream(_request()))

    payload = captured[0]
    assert payload["system"] == [{"type": "text", "text": "you are orin", "cache_control": {"type": "ephemeral"}}]
    assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in payload["tools"][0]


def test_anthropic_payload_marks_the_last_message_block_as_cacheable() -> None:
    """The growing conversation is the bulk of every request's tokens and is
    resent on every loop iteration and every later turn -- it must be
    cacheable too, not just system/tools."""
    captured: list[dict] = []
    list(_transport("anthropic", captured).stream(_request()))

    payload = captured[0]
    assert payload["messages"][-1]["content"] == [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]


def test_anthropic_payload_caches_the_last_block_of_list_shaped_content() -> None:
    captured: list[dict] = []
    request = _request()
    request["messages"] = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "search", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}, {"type": "text", "text": "and also this"}]},
    ]

    list(_transport("anthropic", captured).stream(request))

    last_content = captured[0]["messages"][-1]["content"]
    assert last_content[0] == {"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}
    assert last_content[-1] == {"type": "text", "text": "and also this", "cache_control": {"type": "ephemeral"}}


def test_anthropic_payload_does_not_mutate_the_caller_supplied_messages() -> None:
    """The runtime keeps and reappends these exact dicts across loop
    iterations; if the transport mutated them in place, a stale
    cache_control marker would stick around once the message is no longer
    last, quietly eating into the 4-breakpoint-per-request cap."""
    captured: list[dict] = []
    request = _request()
    original_last = request["messages"][-1]

    list(_transport("anthropic", captured).stream(request))

    assert "cache_control" not in original_last
    assert original_last["content"] == "hi"


def test_anthropic_payload_separates_a_dynamic_system_item_from_the_cached_one() -> None:
    """A second system-role item (context-budget trim marker, closing
    instruction) must not be folded into the cached prefix -- doing so would
    invalidate the cache every time its text differs turn to turn."""
    captured: list[dict] = []
    request = _request()
    request["messages"] = [
        {"role": "system", "content": "you are orin"},
        {"role": "system", "content": "3 earlier messages omitted"},
        {"role": "user", "content": "hi"},
    ]

    list(_transport("anthropic", captured).stream(request))

    system = captured[0]["system"]
    assert system[0] == {"type": "text", "text": "you are orin", "cache_control": {"type": "ephemeral"}}
    assert system[1] == {"type": "text", "text": "3 earlier messages omitted"}


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


def test_anthropic_required_tool_choice_is_canonicalized_to_any() -> None:
    captured: list[dict] = []
    request = {**_request(), "tool_choice": "required"}
    list(_transport("anthropic", captured).stream(request))

    assert captured[0]["tool_choice"] == {"type": "any"}
