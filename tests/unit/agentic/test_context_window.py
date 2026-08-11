from __future__ import annotations

from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime


def _runtime(**limits) -> AgenticTurnRuntime:
    return AgenticTurnRuntime(store=object(), provider=object(), system_prompt="prompt", limits=AgenticLimits(**limits))


def test_the_original_user_request_survives_a_full_window() -> None:
    messages = [{"role": "user", "content": "build the report"}]
    messages += [{"role": "assistant", "content": "x" * 4_000} for _ in range(60)]

    window = _runtime(max_context_tokens=2_000)._request_messages(messages)

    assert window[0]["role"] == "system"
    assert any(item.get("content") == "build the report" for item in window)
    assert len(window) < len(messages)


def test_omitted_messages_are_announced_not_silently_dropped() -> None:
    messages = [{"role": "user", "content": "build the report"}]
    messages += [{"role": "assistant", "content": "x" * 4_000} for _ in range(60)]

    window = _runtime(max_context_tokens=2_000)._request_messages(messages)

    assert any("earlier messages omitted" in str(item.get("content", "")) for item in window)


def test_a_short_conversation_is_returned_untouched() -> None:
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

    window = _runtime(max_context_tokens=60_000)._request_messages(messages)

    assert window == [{"role": "system", "content": "prompt"}, *messages]


def test_the_current_turns_request_is_pinned_not_a_stale_first_turn_request() -> None:
    """``messages`` spans the whole conversation, not just this turn (see
    ``history_for_turn``). The pin must lock onto the current (last) user
    request, not the first-turn request that has already been answered."""
    messages = [
        {"role": "user", "content": "stale first turn request"},
        {"role": "assistant", "content": "x" * 4_000},
        {"role": "user", "content": "current turn request"},
    ]
    messages += [{"role": "assistant", "content": "x" * 4_000} for _ in range(60)]

    window = _runtime(max_context_tokens=2_000)._request_messages(messages)

    assert any(item.get("content") == "current turn request" for item in window)
    assert not any(item.get("content") == "stale first turn request" for item in window)


def test_the_pin_does_not_drift_onto_a_tool_result_shaped_as_user_role() -> None:
    """Mirrors what ``run`` does: the pin is resolved once, before the loop
    appends Anthropic-shaped tool-result messages, which also carry
    ``role: "user"``. Re-deriving "the last user message" per call would drift
    onto those; the stored pin must not."""
    messages = [{"role": "user", "content": "current turn request"}]
    runtime = _runtime(max_context_tokens=2_000)
    runtime._pinned_index = runtime._last_user_index(messages)
    messages.append({"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "search", "input": {}}]})
    messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}]})
    messages += [{"role": "assistant", "content": "x" * 4_000} for _ in range(60)]

    window = runtime._request_messages(messages)

    assert any(item.get("content") == "current turn request" for item in window)


def _assert_no_orphan_tool_results(window: list[dict[str, object]]) -> None:
    has_preceding_call = False
    for item in window:
        role = item.get("role")
        content = item.get("content")
        if role == "assistant":
            has_tool_use = isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_use" for block in content)
            if item.get("tool_calls") or has_tool_use:
                has_preceding_call = True
        elif role == "tool":
            assert has_preceding_call, "tool result appeared without a preceding assistant tool-call message"
        elif role == "user" and isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
            assert has_preceding_call, "tool_result block appeared without a preceding assistant tool-call message"


def test_atomic_tool_unit_openai_shape_never_orphans_a_tool_result() -> None:
    """The assistant message carrying ``tool_calls`` is large; the ``role:
    "tool"`` result answering it is tiny. Walking newest-to-oldest would keep
    the small result and then break on the large assistant message, leaving an
    orphaned tool result -- a shape both providers reject. The pair must be
    kept or dropped together."""
    pinned = {"role": "user", "content": "current turn request"}
    assistant_call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "search", "arguments": "x" * 4_000}}],
    }
    tool_result = {"role": "tool", "tool_call_id": "call_1", "content": "ok"}
    messages = [pinned, assistant_call, tool_result]

    window = _runtime(max_context_tokens=1_000)._request_messages(messages)

    _assert_no_orphan_tool_results(window)
    assert not any(item.get("role") == "tool" for item in window)


def test_atomic_tool_unit_anthropic_shape_never_orphans_a_tool_result() -> None:
    """Same scenario as the OpenAI test, but in Anthropic's shape: the
    assistant message has list content with a ``tool_use`` block, and the
    result is a ``role: "user"`` message with a ``tool_result`` block."""
    pinned = {"role": "user", "content": "current turn request"}
    assistant_call = {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "call_1", "name": "search", "input": {"q": "x" * 4_000}}],
    }
    tool_result = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}]}
    messages = [pinned, assistant_call, tool_result]

    window = _runtime(max_context_tokens=1_000)._request_messages(messages)

    _assert_no_orphan_tool_results(window)
    assert not any(
        item.get("role") == "user" and isinstance(item.get("content"), list) and any(block.get("type") == "tool_result" for block in item["content"])
        for item in window
    )


def test_old_tool_results_are_compressed_but_recent_ones_are_kept() -> None:
    messages = [
        {"role": "user", "content": "go"},
        {"role": "tool", "tool_call_id": "a", "content": "old " * 500},
        {"role": "tool", "tool_call_id": "b", "content": "new " * 500},
    ]

    AgenticTurnRuntime._age_tool_results(messages, keep_recent=1)

    assert "compressed" in messages[1]["content"]
    assert len(messages[1]["content"]) < 800
    assert messages[2]["content"] == "new " * 500


def test_anthropic_tool_results_are_compressed_in_their_block_shape() -> None:
    messages = [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "old " * 500}]},
        {"role": "assistant", "content": "thinking"},
    ]

    AgenticTurnRuntime._age_tool_results(messages, keep_recent=0)

    assert "compressed" in messages[0]["content"][0]["content"]
