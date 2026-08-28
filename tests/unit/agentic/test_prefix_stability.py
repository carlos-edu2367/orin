"""The request prefix must only ever grow.

Prompt caching keys on an exact prefix. Any retroactive edit to a message
already sent invalidates every cache entry written at or after it, so a loop
that rewrites its own history pays full price on every iteration no matter how
many breakpoints it marks.
"""
from __future__ import annotations

import json

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime


def _turn(provider: str = "anthropic") -> dict[str, object]:
    return {
        "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
        "provider": provider, "model_id": "model",
        "user_message_id": "user-message-1", "assistant_message_id": "assistant-message-1",
    }


class _Store:
    def __init__(self, provider: str = "anthropic") -> None:
        self.turn = _turn(provider)

    def load(self, turn_id: str): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "faça a tarefa"}]
    def lifecycle(self, turn, state, **payload) -> None: ...
    def delta(self, turn, text) -> None: ...
    def finish(self, turn, *, failed: bool = False, code: str | None = None) -> None: ...


class _RecordingProvider:
    """Asks for a tool `tool_calls` times, then answers. Records every request."""

    def __init__(self, tool_calls: int = 8) -> None:
        self.requests: list[list[dict[str, object]]] = []
        self._budget = tool_calls

    def stream(self, request):
        # The summarizer runs against this same provider. Its request is a
        # different conversation entirely, so recording it would show up as a
        # rewrite of the turn's own prefix.
        if str(request.get("turn_id", "")).endswith(":context-compaction"):
            return normalize_sse(["data: [DONE]"], provider="openrouter")
        # Snapshot: the runtime reuses these dicts, so a live reference would
        # not show a later mutation as a difference.
        self.requests.append(json.loads(json.dumps(request["messages"], default=str)))
        index = len(self.requests)
        if index <= self._budget:
            return normalize_sse([
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-%d","function":'
                '{"name":"read_file","arguments":"{\\"path\\":\\"f%d.txt\\"}"}}]},"finish_reason":"tool_calls"}]}'
                % (index, index),
                "data: [DONE]",
            ], provider="openrouter")
        return normalize_sse([
            'data: {"choices":[{"delta":{"content":"pronto"},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ], provider="openrouter")


class _BulkyToolset:
    """Returns results large enough that the old code would have compressed them."""

    def schemas(self, allowed=None, kinds=None): return []
    def is_read_only(self, name): return True
    def argument_names(self, name): return None

    def invoke(self, name, arguments):
        return ToolOutcome("succeeded", "lido", "X" * 3_000, {})


def _run(provider: _RecordingProvider, *, turn_provider: str = "anthropic", **limits) -> None:
    settings = {"max_iterations": None, "max_actions": None, "max_context_tokens": 200_000}
    settings.update(limits)
    AgenticTurnRuntime(
        store=_Store(turn_provider), provider=provider, toolset=_BulkyToolset(),
        system_prompt="regras", limits=AgenticLimits(**settings),
    ).run("turn-1")


def _shared_prefix(before: list[dict[str, object]], after: list[dict[str, object]]) -> int:
    count = 0
    for left, right in zip(before, after):
        if json.dumps(left, sort_keys=True) != json.dumps(right, sort_keys=True):
            break
        count += 1
    return count


def test_each_request_has_the_previous_one_as_an_exact_prefix() -> None:
    """The invariant the whole trilha rests on."""
    provider = _RecordingProvider(tool_calls=8)
    _run(provider)

    assert len(provider.requests) >= 8
    for index in range(1, len(provider.requests)):
        before, after = provider.requests[index - 1], provider.requests[index]
        assert _shared_prefix(before, after) == len(before), (
            f"request {index + 1} rewrote history: only {_shared_prefix(before, after)} "
            f"of {len(before)} earlier messages survived unchanged"
        )


def _tool_results(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    """Tool results in either provider shape.

    Anthropic carries them as a ``tool_result`` block inside a user message;
    the OpenAI-compatible shape uses ``role: "tool"``.
    """
    found: list[dict[str, object]] = []
    for item in messages:
        if item.get("role") == "tool":
            found.append(item)
            continue
        content = item.get("content")
        if isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "tool_result" for block in content
        ):
            found.append(item)
    return found


def test_a_tool_result_is_never_edited_after_being_sent() -> None:
    provider = _RecordingProvider(tool_calls=6)
    _run(provider)

    first_result = _tool_results(provider.requests[1])[0]
    for request in provider.requests[2:]:
        assert _tool_results(request)[0] == first_result


def test_compaction_is_the_only_thing_that_rewrites_the_prefix() -> None:
    """A compaction has to invalidate the cache; nothing else may."""
    provider = _RecordingProvider(tool_calls=8)
    _run(provider, max_context_tokens=6_000)

    rewrites = [
        index for index in range(1, len(provider.requests))
        if _shared_prefix(provider.requests[index - 1], provider.requests[index]) < len(provider.requests[index - 1])
    ]
    assert rewrites, "this budget was supposed to force at least one compaction"
    # Every rewrite is a compaction: the request that follows one carries the
    # compaction summary.
    for index in rewrites:
        assert any("Contexto compactado" in str(item.get("content", "")) for item in provider.requests[index])


def test_a_provider_without_caching_still_shrinks_old_results() -> None:
    """Without a cache to protect, shrinking is pure gain and must be kept."""
    provider = _RecordingProvider(tool_calls=6)
    _run(provider, turn_provider="ollama")

    last = provider.requests[-1]
    aged = [item for item in _tool_results(last) if "compressed" in str(item.get("content", ""))]
    assert aged, "an uncached provider should still see old results compressed"
