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
