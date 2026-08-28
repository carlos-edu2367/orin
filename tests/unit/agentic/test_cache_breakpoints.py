"""Where the cache breakpoints go.

Anthropic allows four. They are only worth spending on a prefix that will
still be byte-identical next time, so the stable half of the system prompt
and the tool schemas get one each, and the messages get two: the most recent
stable boundary and the tail.
"""
from __future__ import annotations

from agentos.agentic.provider_stream import HTTPProviderStreamTransport


def _transport() -> HTTPProviderStreamTransport:
    return HTTPProviderStreamTransport(
        provider="anthropic", base_url="https://api.anthropic.com/v1", api_key="k", model="claude",
    )


def _marked(blocks: object) -> int:
    if not isinstance(blocks, list):
        return 0
    total = 0
    for block in blocks:
        if isinstance(block, dict) and block.get("cache_control"):
            total += 1
        content = block.get("content") if isinstance(block, dict) else None
        if isinstance(content, list):
            total += sum(1 for item in content if isinstance(item, dict) and item.get("cache_control"))
    return total


def _request(messages: list[dict[str, object]], tools: list[dict[str, object]] | None = None):
    _url, _headers, payload = _transport()._anthropic_request(messages, tools or [], None, 1024)
    return payload


def _conversation(units: int) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "regras estáveis"},
        {"role": "system", "content": "contexto volátil"},
        {"role": "user", "content": "faça a tarefa"},
    ]
    for index in range(units):
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"c{index}", "name": "read_file", "input": {}},
        ]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"c{index}", "content": "X" * 500},
        ]})
    return messages


def test_only_the_stable_system_block_is_cached() -> None:
    payload = _request(_conversation(2))
    system = payload["system"]
    assert system[0].get("cache_control")
    assert not system[1].get("cache_control"), "the volatile block must not anchor a cache entry"


def test_the_tool_schemas_still_carry_a_breakpoint() -> None:
    payload = _request(_conversation(2), [{"name": "read_file", "description": "d", "input_schema": {}}])
    assert payload["tools"][-1].get("cache_control")


def test_the_tail_is_marked() -> None:
    payload = _request(_conversation(3))
    assert _marked([payload["messages"][-1]]) == 1


def test_an_earlier_stable_boundary_is_also_marked() -> None:
    """The tail alone dies with the next append; this one survives it."""
    payload = _request(_conversation(4))
    marked = [index for index, item in enumerate(payload["messages"]) if _marked([item])]
    assert len(marked) == 2
    assert marked[0] < len(payload["messages"]) - 1


def test_the_four_breakpoint_budget_is_never_exceeded() -> None:
    payload = _request(_conversation(9), [{"name": "read_file", "description": "d", "input_schema": {}}])
    total = (
        sum(1 for item in payload["system"] if item.get("cache_control"))
        + sum(1 for item in payload["tools"] if item.get("cache_control"))
        + _marked(payload["messages"])
    )
    assert total <= 4


def test_a_short_conversation_marks_only_the_tail() -> None:
    """With nothing stable behind it, a second message breakpoint buys nothing."""
    payload = _request([{"role": "system", "content": "regras"}, {"role": "user", "content": "oi"}])
    assert _marked(payload["messages"]) == 1


def test_the_marks_do_not_leak_back_into_the_caller_s_messages() -> None:
    """The runtime reuses these dicts across iterations; a stale mark would
    silently burn one of the four slots forever."""
    messages = _conversation(3)
    _request(messages)
    assert _marked(messages) == 0
