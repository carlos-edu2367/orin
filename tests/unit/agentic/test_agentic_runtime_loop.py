from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import httpx

from agentos.agentic.action_loop import ActionLoop, MalformedToolCall
from agentos.agentic.provider_stream import (
    HTTPProviderStreamTransport,
    NormalizedStreamItem,
    StreamKind,
    normalize_sse,
    project_rate_limit_headers,
)
from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.providers.models import ProviderUsage


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-1",
        "conversation_id": "conversation-1",
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "agent_id": "agent-1",
        "execution_id": "execution-1",
        "provider": "openrouter",
        "model_id": "local-model",
        "user_message_id": "user-message-1",
        "assistant_message_id": "assistant-message-1",
    }


class Store:
    def __init__(self) -> None:
        self.turn = _turn()
        self.events: list[tuple[str, dict[str, object]]] = []
        self.deltas: list[str] = []
        self.usages: list[dict[str, int | None]] = []
        self.finished: list[tuple[bool, str | None]] = []

    def load(self, turn_id: str) -> dict[str, object]:
        assert turn_id == self.turn["turn_id"]
        return self.turn

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, str]]:
        return [{"role": "user", "content": "use the safe tool"}]

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self.events.append((state, payload))

    def delta(self, turn: dict[str, object], text: str) -> None:
        self.deltas.append(text)

    def record_usage(self, turn: dict[str, object], *, input_tokens: int | None, output_tokens: int | None, total_tokens: int | None) -> None:
        self.usages.append({"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens})

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        self.finished.append((failed, code))


class Provider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def stream(self, request: dict[str, object]):
        self.calls.append(request)
        if len(self.calls) == 1:
            return normalize_sse(
                [
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"lookup","arguments":"{\\"q\\":\\"safe\\"}"}}]},"finish_reason":"tool_calls"}]}',
                    "data: [DONE]",
                ],
                provider="openrouter",
            )
        return normalize_sse(
            [
                'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ],
            provider="openrouter",
        )


class ActionRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def invoke(self, request):
        self.calls.append(dict(request.arguments))
        return SimpleNamespace(
            __class__=object,
            result_ref="result:1",
            result={"secret": "must-not-leak"},
        )


class Registry:
    def list(self, context):
        return (
            SimpleNamespace(
                name="lookup",
                description="Look up a bounded value",
                input_schema={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                    "additionalProperties": False,
                },
                tool_ref=SimpleNamespace(tool_id="lookup", version=1),
            ),
        )

    def resolve(self, name, context):
        return self.list(context)[0]


def test_runtime_executes_tool_then_continues_and_redacts_result() -> None:
    store, provider, action_runtime = Store(), Provider(), ActionRuntime()
    action_loop = ActionLoop(Registry(), action_runtime)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        actions=action_loop,
        limits=AgenticLimits(max_actions=2, max_iterations=3, deadline=timedelta(seconds=5)),
        clock=lambda: datetime.now(UTC),
    )

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert action_runtime.calls == [{"q": "safe"}]
    assert store.deltas == ["answer"]
    assert store.finished == [(False, None)]
    assert [state for state, _ in store.events] == ["running", "waiting_tool", "tool_started", "tool_finished", "running", "completed"]
    assert "must-not-leak" not in repr(provider.calls)


def test_runtime_publishes_each_text_delta_before_reading_the_next_stream_event() -> None:
    store = Store()

    class ChunkedProvider:
        def __init__(self) -> None:
            self.first_chunk_was_published = False

        def stream(self, request):
            yield NormalizedStreamItem(StreamKind.TEXT, 1, text="primeiro ")
            self.first_chunk_was_published = store.deltas == ["primeiro "]
            yield NormalizedStreamItem(StreamKind.TEXT, 2, text="segundo")
            yield NormalizedStreamItem(StreamKind.FINISH, 3, finish_reason="stop")

    provider = ChunkedProvider()
    runtime = AgenticTurnRuntime(store=store, provider=provider, actions=ActionLoop(Registry(), ActionRuntime()))

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert provider.first_chunk_was_published is True
    assert store.deltas == ["primeiro ", "segundo"]


def test_runtime_does_not_persist_usage_that_exceeds_the_serialized_request() -> None:
    class InflatedUsageProvider:
        def stream(self, request):
            return [
                NormalizedStreamItem(StreamKind.TEXT, 1, text="curta"),
                NormalizedStreamItem(StreamKind.USAGE, 2, usage=ProviderUsage(63_480, 3_570, 67_050, measurement="CONFIRMED")),
                NormalizedStreamItem(StreamKind.FINISH, 3, finish_reason="stop"),
            ]

    store = Store()
    runtime = AgenticTurnRuntime(store=store, provider=InflatedUsageProvider(), actions=ActionLoop(Registry(), ActionRuntime()))

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert store.usages == []


def test_action_loop_rejects_duplicate_and_malformed_calls_before_execution() -> None:
    loop = ActionLoop(Registry(), ActionRuntime())
    context = SimpleNamespace(user_id="user-1")

    with pytest.raises(MalformedToolCall):
        loop.execute(
            [
                {"id": "call-1", "name": "lookup", "arguments": '{"q":"x"}'},
                {"id": "call-1", "name": "lookup", "arguments": '{"q":"x"}'},
            ],
            context,
            action_count=0,
            max_actions=3,
        )

    with pytest.raises(MalformedToolCall):
        loop.execute([{"id": "call-2", "name": "lookup", "arguments": "not-json"}], context, action_count=0, max_actions=3)


def test_stream_normalizer_handles_usage_finish_rate_limit_and_anthropic_tool_delta() -> None:
    openai = list(
        normalize_sse(
            [
                'data: {"choices":[{"delta":{"content":"hi"}}]}',
                'data: {"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5},"choices":[{"finish_reason":"stop"}]}',
            ],
            provider="openai",
        )
    )
    anthropic = list(
        normalize_sse(
            [
                'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"a1","name":"lookup"}}',
                'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"q\\":\\"x\\"}"}}',
                'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":1}}',
            ],
            provider="anthropic",
        )
    )

    assert [item.kind for item in openai] == [StreamKind.TEXT, StreamKind.USAGE, StreamKind.FINISH]
    assert [item.kind for item in anthropic] == [StreamKind.TOOL_CALL, StreamKind.USAGE, StreamKind.FINISH]
    assert anthropic[0].tool_name == "lookup"
    assert project_rate_limit_headers({"x-ratelimit-remaining": "2", "x-ratelimit-reset": "10"}).remaining == 2


def test_runtime_cancellation_is_terminal_and_requests_provider_cancel() -> None:
    class CancellableProvider(Provider):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled = 0

        def cancel(self, request):
            self.cancelled += 1

        def stream(self, request):
            raise AssertionError("cancelled turns must not start a provider stream")

    provider = CancellableProvider()
    store = Store()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        actions=ActionLoop(Registry(), ActionRuntime()),
        cancelled=lambda _turn: True,
    )

    result = runtime.run("turn-1")

    assert result.state == "cancelled"
    assert provider.cancelled == 1
    assert store.finished == [(True, "TURN_CANCELLED")]


def test_runtime_enforces_cost_budget_from_normalized_usage() -> None:
    class CostProvider:
        def stream(self, request):
            return [NormalizedStreamItem(StreamKind.USAGE, 1, cost=Decimal("2")), NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")]

    store = Store()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=CostProvider(),
        actions=ActionLoop(Registry(), ActionRuntime()),
        limits=AgenticLimits(max_cost=Decimal("1")),
    )

    result = runtime.run("turn-1")

    assert result.state == "failed"
    assert result.error_code == "PROVIDER_COST_LIMIT"


def test_http_transport_keeps_rate_limit_and_sse_sequences_monotonic() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-ratelimit-remaining": "1", "content-type": "text/event-stream"},
            content='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
        )

    transport = HTTPProviderStreamTransport(
        provider="openai",
        base_url="https://provider.test/v1",
        api_key="secret",
        model="local",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    items = list(transport.stream({"messages": [], "tools": []}))

    assert [item.sequence for item in items] == [1, 2, 3]


def test_runtime_retries_a_rate_limited_stream_within_the_turn_budget() -> None:
    class RateLimitedProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                return [NormalizedStreamItem(StreamKind.RATE_LIMIT, 1)]
            return [NormalizedStreamItem(StreamKind.TEXT, 1, text="recovered"), NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")]

    provider = RateLimitedProvider()
    store = Store()
    runtime = AgenticTurnRuntime(store=store, provider=provider, actions=ActionLoop(Registry(), ActionRuntime()), limits=AgenticLimits(max_provider_retries=1))

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert provider.calls == 2
    assert any(state == "retrying" for state, _ in store.events)


def test_runtime_allows_an_unlimited_tool_iteration_budget() -> None:
    class ManyToolsProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls <= 13:
                return [
                    NormalizedStreamItem(StreamKind.TOOL_CALL, 1, tool_call_id=f"tool-{self.calls}", tool_name="noop", arguments_delta="{}"),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
                ]
            return [NormalizedStreamItem(StreamKind.TEXT, 1, text="completed"), NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")]

    class Toolset:
        def schemas(self): return []
        def invoke(self, name, arguments): return ToolOutcome("succeeded", "noop", "ok")

    provider = ManyToolsProvider()
    result = AgenticTurnRuntime(
        store=Store(), provider=provider, toolset=Toolset(),
        limits=AgenticLimits(max_iterations=None, max_actions=20, deadline=timedelta(seconds=5)),
    ).run("turn-1")

    assert result.state == "completed"
    assert provider.calls == 14


def test_runtime_audits_unknown_tool_call_as_failed_action() -> None:
    class UnknownToolProvider:
        def stream(self, request):
            return [
                NormalizedStreamItem(StreamKind.TOOL_CALL, 1, tool_call_id="bad-1", tool_name="unknown", arguments_delta="{}"),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
            ]

    store = Store()
    runtime = AgenticTurnRuntime(store=store, provider=UnknownToolProvider(), actions=ActionLoop(Registry(), ActionRuntime()))

    result = runtime.run("turn-1")

    assert result.state == "failed"
    assert any(state == "tool_failed" and payload["code"] == "MALFORMED_OR_DUPLICATE_TOOL_CALL" for state, payload in store.events)


def test_read_only_calls_run_concurrently_and_keep_their_order() -> None:
    import threading
    import time

    barrier = threading.Barrier(2, timeout=5)

    class SlowToolset:
        def schemas(self):
            return []

        def is_read_only(self, name: str) -> bool:
            return True

        def invoke(self, name, arguments):
            # Both calls must be in flight at once or this times out.
            barrier.wait()
            time.sleep(0.01)
            return ToolOutcome("succeeded", f"{name} ok", f"content-{arguments['n']}")

    class TwoCallProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                return normalize_sse(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":['
                        '{"index":0,"id":"call-1","function":{"name":"read_file","arguments":"{\\"n\\":1}"}},'
                        '{"index":1,"id":"call-2","function":{"name":"read_file","arguments":"{\\"n\\":2}"}}'
                        ']},"finish_reason":"tool_calls"}]}',
                        "data: [DONE]",
                    ],
                    provider="openrouter",
                )
            return normalize_sse(['data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")

    store = Store()
    runtime = AgenticTurnRuntime(store=store, provider=TwoCallProvider(), toolset=SlowToolset())

    result = runtime.run("turn-1")

    assert result.state == "completed"
    finished = [payload for state, payload in store.events if state == "tool_finished"]
    assert [item["invocation_id"] for item in finished] == ["call-1", "call-2"]


def test_a_write_call_is_not_reordered_around_read_only_calls_that_follow_it() -> None:
    class OrderRecordingToolset:
        def __init__(self) -> None:
            self.execution_order: list[str] = []

        def schemas(self):
            return []

        def is_read_only(self, name: str) -> bool:
            return name == "read_file"

        def invoke(self, name, arguments):
            self.execution_order.append(name)
            return ToolOutcome("succeeded", f"{name} ok", "content")

    class MixedBatchProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                return normalize_sse(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":['
                        '{"index":0,"id":"call-1","function":{"name":"write_file","arguments":"{}"}},'
                        '{"index":1,"id":"call-2","function":{"name":"read_file","arguments":"{}"}},'
                        '{"index":2,"id":"call-3","function":{"name":"read_file","arguments":"{}"}}'
                        ']},"finish_reason":"tool_calls"}]}',
                        "data: [DONE]",
                    ],
                    provider="openrouter",
                )
            return normalize_sse(['data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")

    toolset = OrderRecordingToolset()
    store = Store()
    runtime = AgenticTurnRuntime(store=store, provider=MixedBatchProvider(), toolset=toolset)

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert toolset.execution_order == ["write_file", "read_file", "read_file"]


def test_the_last_iteration_forbids_tools_and_returns_an_answer() -> None:
    class AlwaysToolsProvider:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def stream(self, request):
            self.calls.append(request)
            if request.get("tool_choice") == "none":
                return normalize_sse(['data: {"choices":[{"delta":{"content":"partial answer"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")
            return normalize_sse(
                [
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-%d","function":{"name":"read_file","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}' % len(self.calls),
                    "data: [DONE]",
                ],
                provider="openrouter",
            )

    class EchoToolset:
        def schemas(self):
            return []

        def is_read_only(self, name: str) -> bool:
            return True

        def invoke(self, name, arguments):
            return ToolOutcome("succeeded", "ok", "content")

    store = Store()
    provider = AlwaysToolsProvider()
    runtime = AgenticTurnRuntime(store=store, provider=provider, toolset=EchoToolset(), limits=AgenticLimits(max_iterations=3, max_actions=8))

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert store.deltas == ["partial answer"]
    assert provider.calls[-1]["tool_choice"] == "none"
    assert provider.calls[0].get("tool_choice") is None
    assert result.budget_exhausted is True


def test_an_identical_failing_call_is_not_executed_twice() -> None:
    class RepeatingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls <= 2:
                return normalize_sse(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-%d","function":{"name":"read_file","arguments":"{\\"path\\":\\"nope\\"}"}}]},"finish_reason":"tool_calls"}]}' % self.calls,
                        "data: [DONE]",
                    ],
                    provider="openrouter",
                )
            return normalize_sse(['data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")

    class CountingToolset:
        def __init__(self) -> None:
            self.invocations = 0

        def schemas(self):
            return []

        def is_read_only(self, name: str) -> bool:
            return True

        def invoke(self, name, arguments):
            self.invocations += 1
            return ToolOutcome("failed", "não encontrado", "file not found", {}, "TOOL_REFUSED")

    toolset = CountingToolset()
    runtime = AgenticTurnRuntime(store=Store(), provider=RepeatingProvider(), toolset=toolset)

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert toolset.invocations == 1


def test_a_final_iteration_with_only_tool_calls_and_no_text_still_fails_with_iteration_limit() -> None:
    """The provider may ignore tool_choice="none" and request tools anyway.

    If that happens on the last allowed iteration and no text comes with it,
    the turn must fail with ITERATION_LIMIT exactly as it did before the
    closing-instruction change, rather than reporting an empty "completed"
    turn with the model's tool calls silently discarded.
    """

    class IgnoresToolChoiceProvider:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def stream(self, request):
            self.calls.append(request)
            return normalize_sse(
                [
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"read_file","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}',
                    "data: [DONE]",
                ],
                provider="openrouter",
            )

    class EchoToolset:
        def schemas(self):
            return []

        def is_read_only(self, name: str) -> bool:
            return True

        def invoke(self, name, arguments):
            return ToolOutcome("succeeded", "ok", "content")

    store = Store()
    provider = IgnoresToolChoiceProvider()
    runtime = AgenticTurnRuntime(store=store, provider=provider, toolset=EchoToolset(), limits=AgenticLimits(max_iterations=1, max_actions=8))

    result = runtime.run("turn-1")

    assert result.state == "failed"
    assert result.error_code == "ITERATION_LIMIT"
    assert store.deltas == []
