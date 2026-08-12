from __future__ import annotations

import json

import httpx
import pytest

from agentos.agentic.provider_stream import ANTHROPIC_REQUIRED_MAX_TOKENS, HTTPProviderStreamTransport, StreamKind, normalize_ndjson
from agentos.providers.models import FinishReason


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


def test_ndjson_stream_yields_text_then_usage_and_a_finish() -> None:
    lines = [
        json.dumps({"message": {"role": "assistant", "content": "hel"}, "done": False}),
        json.dumps({"message": {"role": "assistant", "content": "lo"}, "done": False}),
        json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                    "done_reason": "stop", "prompt_eval_count": 31, "eval_count": 7}),
    ]

    items = list(normalize_ndjson(lines))

    assert [item.kind for item in items] == [StreamKind.TEXT, StreamKind.TEXT, StreamKind.USAGE, StreamKind.FINISH]
    assert "".join(item.text or "" for item in items) == "hello"
    assert items[2].usage.input_tokens == 31
    assert items[2].usage.output_tokens == 7
    assert items[2].usage.total_tokens == 38
    assert items[3].finish_reason is FinishReason.STOP
    assert [item.sequence for item in items] == [1, 2, 3, 4]


def test_ndjson_gives_each_tool_call_its_own_id() -> None:
    """Ollama sends no call id, and two calls sharing one would be merged."""
    lines = [
        json.dumps({"message": {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}}]}, "done": False}),
        json.dumps({"message": {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "b.txt"}}}]}, "done": False}),
        json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 2}),
    ]

    items = [item for item in normalize_ndjson(lines) if item.kind is StreamKind.TOOL_CALL]

    assert [item.tool_call_id for item in items] == ["tool-call:1", "tool-call:2"]
    assert [item.tool_name for item in items] == ["read_file", "read_file"]
    assert [json.loads(item.arguments_delta or "{}") for item in items] == [{"path": "a.txt"}, {"path": "b.txt"}]


def test_ndjson_finishes_as_tool_calls_when_the_model_asked_for_one() -> None:
    """Ollama reports done_reason "stop" even for a turn that ends in a call."""
    lines = [
        json.dumps({"message": {"tool_calls": [{"function": {"name": "read_file", "arguments": {}}}]}, "done": False}),
        json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 5, "eval_count": 1}),
    ]

    finish = [item for item in normalize_ndjson(lines) if item.kind is StreamKind.FINISH]

    assert finish[0].finish_reason is FinishReason.TOOL_CALLS


def test_ndjson_reports_a_provider_error_without_echoing_it() -> None:
    items = list(normalize_ndjson([json.dumps({"error": "model 'ghost' not found, api_key=leaked"})]))

    assert items[0].kind is StreamKind.ERROR
    assert "leaked" not in items[0].error.message
    assert items[0].error.message == "provider stream failed"


def test_ndjson_survives_a_malformed_line() -> None:
    lines = ["{not json", json.dumps({"message": {"content": "ok"}, "done": False})]

    items = list(normalize_ndjson(lines))

    assert items[0].kind is StreamKind.ERROR
    assert items[0].error.code == "INVALID_NDJSON"
    assert items[1].kind is StreamKind.TEXT


def test_ndjson_ignores_blank_keepalive_lines() -> None:
    items = list(normalize_ndjson(["", "   ", json.dumps({"message": {"content": "ok"}, "done": False})]))

    assert [item.kind for item in items] == [StreamKind.TEXT]


def _ollama_transport(captured: list[dict], *, num_ctx: int | None = 32_768, api_key: str = "") -> HTTPProviderStreamTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content)})
        return httpx.Response(200, text=json.dumps({"done": True, "done_reason": "stop"}) + "\n")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HTTPProviderStreamTransport(provider="ollama", base_url="http://localhost:11434", api_key=api_key, model="qwen3:8b", client=client, num_ctx=num_ctx)


def test_ollama_posts_to_the_native_chat_endpoint_with_the_context_size() -> None:
    captured: list[dict] = []

    list(_ollama_transport(captured).stream(_request()))

    assert captured[0]["url"] == "http://localhost:11434/api/chat"
    assert captured[0]["body"]["stream"] is True
    assert captured[0]["body"]["options"]["num_ctx"] == 32_768
    assert captured[0]["body"]["options"]["num_predict"] == 512
    # The system prompt stays inline; the native API has no separate field.
    assert captured[0]["body"]["messages"][0] == {"role": "system", "content": "you are orin"}


def test_ollama_omits_the_options_it_was_not_given() -> None:
    captured: list[dict] = []
    request = {key: value for key, value in _request().items() if key != "max_output_tokens"}

    list(_ollama_transport(captured, num_ctx=None).stream(request))

    assert "options" not in captured[0]["body"]


def test_ollama_forwards_the_tool_declarations_unchanged() -> None:
    """Orin already emits the exact shape Ollama's native API expects."""
    captured: list[dict] = []

    list(_ollama_transport(captured).stream(_request()))

    assert captured[0]["body"]["tools"] == _request()["tools"]
    assert "tool_choice" not in captured[0]["body"]


def test_ollama_withholds_the_tools_on_the_closing_iteration() -> None:
    """Ollama has no tool_choice, so "none" is honored by sending no tools."""
    captured: list[dict] = []

    list(_ollama_transport(captured).stream({**_request(), "tool_choice": "none"}))

    assert "tools" not in captured[0]["body"]


def test_ollama_keeps_the_tools_when_tool_choice_is_absent() -> None:
    """A None tool_choice must not be mistaken for the literal string "none"."""
    captured: list[dict] = []

    list(_ollama_transport(captured).stream({**_request(), "tool_choice": None}))

    assert captured[0]["body"]["tools"] == _request()["tools"]


def test_ollama_authenticates_only_when_a_cloud_key_is_configured() -> None:
    local: list[dict] = []
    list(_ollama_transport(local).stream(_request()))
    assert "authorization" not in local[0]["headers"]

    cloud: list[dict] = []
    list(_ollama_transport(cloud, api_key="cloud-secret").stream(_request()))
    assert cloud[0]["headers"]["authorization"] == "Bearer cloud-secret"


def test_ollama_stream_is_normalized_as_ndjson() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="\n".join([
            json.dumps({"message": {"content": "hi"}, "done": False}),
            json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 4, "eval_count": 1}),
        ]))

    transport = HTTPProviderStreamTransport(
        provider="ollama", base_url="http://localhost:11434", api_key="", model="qwen3:8b",
        client=httpx.Client(transport=httpx.MockTransport(handler)), num_ctx=8192,
    )

    items = list(transport.stream(_request()))

    assert [item.kind for item in items] == [StreamKind.TEXT, StreamKind.USAGE, StreamKind.FINISH]
    assert items[0].text == "hi"
