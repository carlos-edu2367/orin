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


def test_a_user_question_ends_the_provider_run_without_waiting_for_a_worker() -> None:
    class QuestionProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            return normalize_sse([
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"ask-1","function":{"name":"ask_user","arguments":"{\\"questions\\":[]}"}}]},"finish_reason":"tool_calls"}]}',
                "data: [DONE]",
            ], provider="openrouter")

    class QuestionToolset:
        def schemas(self): return []
        def is_read_only(self, name): return False
        def argument_names(self, name): return frozenset({"questions"})
        def invoke(self, name, arguments):
            assert name == "ask_user" and arguments == {"questions": []}
            return ToolOutcome("succeeded", "Aguardando resposta", "wait", {"wait_for_user": True})

    store = Store()
    provider = QuestionProvider()
    result = AgenticTurnRuntime(store=store, provider=provider, toolset=QuestionToolset()).run("turn-1")

    assert result.state == "waiting_user"
    assert provider.calls == 1
    assert store.finished == [(False, "WAITING_USER")]
    assert any(state == "waiting_user" for state, _ in store.events)


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


def test_runtime_preserves_ollama_thinking_on_the_next_tool_request() -> None:
    store = Store()
    store.turn["provider"] = "ollama"

    class ThinkingProvider:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def stream(self, request):
            self.calls.append(request)
            if len(self.calls) == 1:
                return iter([
                    NormalizedStreamItem(StreamKind.TOOL_CALL, 1, thinking="inspect the file", tool_call_id="call-1", tool_name="lookup", arguments_delta='{"q":"safe"}'),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
                ])
            return iter([
                NormalizedStreamItem(StreamKind.TEXT, 1, text="done"),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
            ])

    provider = ThinkingProvider()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        actions=ActionLoop(Registry(), ActionRuntime()),
        limits=AgenticLimits(max_actions=2, max_iterations=3, deadline=timedelta(seconds=5)),
        clock=lambda: datetime.now(UTC),
    )

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert provider.calls[1]["messages"][1]["thinking"] == "inspect the file"


def test_runtime_allows_unlimited_actions_when_configured_as_none() -> None:
    class ManyToolsProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls <= 13:
                yield NormalizedStreamItem(StreamKind.TOOL_CALL, self.calls, tool_call_id=f"call-{self.calls}", tool_name="lookup", arguments_delta='{"q":"safe"}')
                yield NormalizedStreamItem(StreamKind.FINISH, self.calls + 100, finish_reason="tool_calls")
            else:
                yield NormalizedStreamItem(StreamKind.TEXT, self.calls + 100, text="done")
                yield NormalizedStreamItem(StreamKind.FINISH, self.calls + 101, finish_reason="stop")

    provider = ManyToolsProvider()
    runtime = AgenticTurnRuntime(
        store=Store(),
        provider=provider,
        actions=ActionLoop(Registry(), ActionRuntime()),
        limits=AgenticLimits(max_iterations=None, max_actions=None, deadline=timedelta(seconds=5)),
    )

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert result.actions == 13


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


def test_runtime_retries_a_provider_stream_that_cannot_start() -> None:
    class FlakyProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                raise OSError("provider is temporarily unavailable")
            return [NormalizedStreamItem(StreamKind.TEXT, 1, text="recovered"), NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")]

    provider = FlakyProvider()
    store = Store()
    result = AgenticTurnRuntime(store=store, provider=provider, limits=AgenticLimits(max_provider_retries=1)).run("turn-1")

    assert result.state == "completed"
    assert provider.calls == 2
    assert any(state == "retrying" for state, _ in store.events)


def test_runtime_pauses_for_reconciliation_when_a_stream_breaks_after_output() -> None:
    class InterruptedProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            yield NormalizedStreamItem(StreamKind.TEXT, 1, text="partial response")
            raise OSError("connection reset")

    provider = InterruptedProvider()
    store = Store()
    result = AgenticTurnRuntime(
        store=store,
        provider=provider,
        limits=AgenticLimits(max_provider_retries=1),
        sleep=lambda _s: None,
    ).run("turn-1")

    assert result.state == "reconciliation_required"
    assert result.error_code == "PROVIDER_STREAM_INTERRUPTED"
    # 1 initial call + MAX_STREAM_RESUMES (2) clean-ledger auto-resumes before pausing.
    assert provider.calls == 3
    assert store.deltas == ["partial response", "partial response", "partial response"]
    assert result.end_reason is not None
    assert result.message is not None


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


def test_parallel_tool_exceptions_become_failed_results_without_losing_sibling_results() -> None:
    class ExceptionToolset:
        def schemas(self):
            return []

        def is_read_only(self, name: str) -> bool:
            return True

        def invoke(self, name, arguments):
            if name == "explode":
                raise RuntimeError("worker exploded")
            return ToolOutcome("succeeded", "ok", "sibling result")

    class TwoCallProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                return normalize_sse(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":['
                        '{"index":0,"id":"call-1","function":{"name":"explode","arguments":"{}"}},'
                        '{"index":1,"id":"call-2","function":{"name":"succeed","arguments":"{}"}}'
                        ']},"finish_reason":"tool_calls"}]}',
                        "data: [DONE]",
                    ],
                    provider="openrouter",
                )
            return normalize_sse(
                ['data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}', "data: [DONE]"],
                provider="openrouter",
            )

    store = Store()
    runtime = AgenticTurnRuntime(store=store, provider=TwoCallProvider(), toolset=ExceptionToolset())

    result = runtime.run("turn-1")

    assert result.state == "completed"
    finished = [payload for state, payload in store.events if state == "tool_finished"]
    assert [item["status"] for item in finished] == ["failed", "succeeded"]
    assert "worker exploded" in str(finished[0].get("summary")) or finished[0].get("error_code") == "TOOL_FAILED"


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
                        '{"index":2,"id":"call-3","function":{"name":"read_file","arguments":"{\\"path\\":\\"b\\"}"}}'
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


class _RequestRecordingProvider:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def stream(self, request):
        self.requests.append(dict(request))
        return normalize_sse(['data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")


def test_no_output_cap_is_requested_when_none_is_configured() -> None:
    provider = _RequestRecordingProvider()
    runtime = AgenticTurnRuntime(store=Store(), provider=provider)

    assert runtime.run("turn-1").state == "completed"
    assert provider.requests[0]["max_output_tokens"] is None


def test_a_provider_token_budget_still_caps_the_request() -> None:
    provider = _RequestRecordingProvider()
    runtime = AgenticTurnRuntime(store=Store(), provider=provider, limits=AgenticLimits(max_provider_tokens=700))

    assert runtime.run("turn-1").state == "completed"
    assert provider.requests[0]["max_output_tokens"] == 700


def test_an_output_cap_is_forwarded_when_it_is_configured() -> None:
    provider = _RequestRecordingProvider()
    runtime = AgenticTurnRuntime(store=Store(), provider=provider, limits=AgenticLimits(max_output_tokens=256))

    assert runtime.run("turn-1").state == "completed"
    assert provider.requests[0]["max_output_tokens"] == 256


def test_a_stylesheet_written_with_unescaped_quotes_still_reaches_the_tool() -> None:
    """A model writing CSS breaks its own JSON; the turn must survive it."""

    class RecordingToolset:
        def __init__(self) -> None:
            self.arguments: list[dict[str, object]] = []

        def schemas(self):
            return []

        def is_read_only(self, name: str) -> bool:
            return False

        def argument_names(self, name: str):
            return frozenset({"path", "content", "mode"}) if name == "write_file" else None

        def invoke(self, name, arguments):
            self.arguments.append(dict(arguments))
            return ToolOutcome("succeeded", f"{name} ok", "written")

    # Literal newlines, an unescaped font name and a CSS codepoint escape, exactly
    # as a provider streams them when the model does not escape its own payload.
    broken = '{"path": "styles.css", "content": "body {\\n  font-family: "Inter", sans-serif;\\n}\\n.q::before { content: "\\\\201C"; }"}'
    broken = broken.replace("\\n", "\n")

    class CssProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                return [
                    NormalizedStreamItem(StreamKind.TOOL_CALL, 1, tool_call_id="call-1", tool_name="write_file", arguments_delta=broken),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
                ]
            return normalize_sse(['data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")

    toolset = RecordingToolset()
    runtime = AgenticTurnRuntime(store=Store(), provider=CssProvider(), toolset=toolset)

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert toolset.arguments == [{
        "path": "styles.css",
        "content": 'body {\n  font-family: "Inter", sans-serif;\n}\n.q::before { content: "\\201C"; }',
    }]


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


def test_a_clean_first_response_at_maximum_iteration_is_not_budget_exhausted() -> None:
    class CleanProvider:
        def stream(self, request):
            return normalize_sse(
                ['data: {"choices":[{"delta":{"content":"complete"},"finish_reason":"stop"}]}', "data: [DONE]"],
                provider="openrouter",
            )

    result = AgenticTurnRuntime(
        store=Store(),
        provider=CleanProvider(),
        toolset=type("Toolset", (), {"schemas": lambda self: []})(),
        limits=AgenticLimits(max_iterations=1),
    ).run("turn-1")

    assert result.state == "completed"
    assert result.budget_exhausted is False


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


def test_a_handled_failure_from_a_mutating_tool_does_not_pause_for_effect_reconciliation() -> None:
    class DelegationProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                return normalize_sse(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"delegate-1","function":{"name":"ask_agents","arguments":"{\\"tasks\\":[]}"}}]},"finish_reason":"tool_calls"}]}',
                        "data: [DONE]",
                    ],
                    provider="openrouter",
                )
            return normalize_sse(
                ['data: {"choices":[{"delta":{"content":"I adjusted the delegation and continued."},"finish_reason":"stop"}]}', "data: [DONE]"],
                provider="openrouter",
            )

    class DelegationToolset:
        def schemas(self):
            return []

        def is_read_only(self, name: str) -> bool:
            return False

        def is_mutating(self, name: str) -> bool:
            return True

        def invoke(self, name, arguments):
            assert name == "ask_agents"
            return ToolOutcome("failed", "Limite de subagentes atingido", "Finish the work yourself.", {}, "SUBAGENT_LIMIT")

    class EffectStore(Store):
        def __init__(self) -> None:
            super().__init__()
            self.effects: list[tuple[str, str | None]] = []

        def effect_started(self, turn, *, kind, invocation_ref, request_ref):
            return invocation_ref

        def effect_finished(self, turn, *, effect_id, state, result_ref=None, error_code=None, private_result=None):
            self.effects.append((state, error_code))

    store = EffectStore()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=DelegationProvider(),
        toolset=DelegationToolset(),
        reconciliation_required=lambda _turn: any(state == "UNKNOWN" for state, _ in store.effects),
    )

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert ("NOT_APPLIED", "SUBAGENT_LIMIT") in store.effects
    assert ("UNKNOWN", "SUBAGENT_LIMIT") not in store.effects


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


def test_runtime_close_forwards_to_the_toolset() -> None:
    class ClosableToolset:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    toolset = ClosableToolset()
    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)

    runtime.close()

    assert toolset.closed == 1


def test_stream_interrupted_auto_resumes_with_clean_ledger_and_completes() -> None:
    from agentos.providers.claude_cli.errors import ProviderTransportError

    class DropThenCompleteProvider:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def stream(self, request):
            self.requests.append(dict(request))
            if len(self.requests) == 1:
                yield NormalizedStreamItem(StreamKind.TEXT, 1, text="Parte 1. ")
                raise ProviderTransportError(
                    "CLI sem saída por 180s",
                    end_reason="idle_timeout",
                )
            yield NormalizedStreamItem(StreamKind.TEXT, 1, text="Parte 2 concluída.")
            yield NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")

    provider = DropThenCompleteProvider()
    store = Store()
    sleeps: list[float] = []
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        sleep=lambda seconds: sleeps.append(seconds),
    )

    result = runtime.run("turn-1")

    assert result.state == "completed"
    assert len(provider.requests) == 2
    assert "".join(store.deltas) == "Parte 1. Parte 2 concluída."
    assert sum(sleeps) == pytest.approx(2.0)
    resuming_events = [payload for state, payload in store.events if state == "stream_resuming"]
    assert len(resuming_events) == 1
    assert resuming_events[0]["summary"] == "Conexão com o modelo caiu; retomando (1/2)"
    second_messages = provider.requests[1]["messages"]
    assert any(m.get("role") == "assistant" and "Parte 1." in str(m.get("content")) for m in second_messages)
    assert any("previous model connection was interrupted" in str(m.get("content")) for m in second_messages)


def test_stream_interrupted_with_unresolved_tool_effect_pauses_immediately() -> None:
    from agentos.providers.claude_cli.errors import ProviderTransportError

    class InterruptedAfterToolProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            yield NormalizedStreamItem(StreamKind.TEXT, 1, text="partial before drop")
            raise ProviderTransportError(
                "CLI encerrou sem evento result (exit=9).",
                end_reason="stdout_eof",
                exit_code=9,
            )

    class UnresolvedToolStore(Store):
        def has_unresolved_tool_effects(self, turn) -> bool:
            return True

    provider = InterruptedAfterToolProvider()
    store = UnresolvedToolStore()
    result = AgenticTurnRuntime(
        store=store,
        provider=provider,
        sleep=lambda _s: None,
    ).run("turn-1")

    assert result.state == "reconciliation_required"
    assert result.error_code == "PROVIDER_STREAM_INTERRUPTED"
    assert result.end_reason == "stdout_eof"
    assert "código 9" in (result.message or "")
    assert provider.calls == 1


def test_subagent_stream_interrupted_auto_resumes_once_then_stops() -> None:
    from agentos.providers.claude_cli.errors import ProviderTransportError

    class SubagentStore(Store):
        def __init__(self) -> None:
            super().__init__()
            self.text: list[str] = []
            self.agent_id = "sub-1"

        def delta(self, turn: dict[str, object], text: str) -> None:
            super().delta(turn, text)
            self.text.append(text)

    # Case 1: Subagent drops once and succeeds on the 1 allowed auto-resume.
    class DropOnceSubagentProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                yield NormalizedStreamItem(StreamKind.TEXT, 1, text="Subagent partial. ")
                raise ProviderTransportError("EOF", end_reason="stdout_eof", exit_code=1)
            yield NormalizedStreamItem(StreamKind.TEXT, 1, text="Subagent finished.")
            yield NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")

    sub_store_ok = SubagentStore()
    provider_ok = DropOnceSubagentProvider()
    res_ok = AgenticTurnRuntime(
        store=sub_store_ok,
        provider=provider_ok,
        sleep=lambda _s: None,
    ).run("turn-sub-1", turn={"turn_id": "turn-sub-1", "assistant_message_id": "msg-sub-1", "is_subagent": True})
    assert res_ok.state == "completed"
    assert provider_ok.calls == 2
    assert "".join(sub_store_ok.text) == "Subagent partial. Subagent finished."

    # Case 2: Subagent drops twice -> only 1 auto-resume is allowed for subagents.
    class AlwaysDropSubagentProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            yield NormalizedStreamItem(StreamKind.TEXT, 1, text="Subagent partial.")
            raise ProviderTransportError("EOF", end_reason="stdout_eof", exit_code=1)

    sub_store_fail = SubagentStore()
    provider_fail = AlwaysDropSubagentProvider()
    res_fail = AgenticTurnRuntime(
        store=sub_store_fail,
        provider=provider_fail,
        sleep=lambda _s: None,
    ).run("turn-sub-2", turn={"turn_id": "turn-sub-2", "assistant_message_id": "msg-sub-2", "is_subagent": True})
    assert res_fail.state == "reconciliation_required"
    assert provider_fail.calls == 2


def test_paused_turn_with_pending_tool_effect_resumes_via_endpoint_and_completes(tmp_path, monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta
    from agentos.execution.journal import (
        ExecutionEffect,
        ExecutionEffectKind,
        ExecutionEffectRetryability,
        ExecutionEffectState,
        ExecutionJournalScope,
    )
    from agentos.persistence.postgres.execution_journal import PostgresExecutionJournal
    from agentos.workers.chat import ChatWorker
    from tests.integration.server_world import build_server_world

    world = build_server_world(tmp_path, monkeypatch)
    admin = world.admin()
    store = world.services.conversation_application.store
    created = store.create(
        user_id=admin.user_id,
        message="Execute long task",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="resume-test-1",
    )
    conv_id = str(created.conversation_id)
    turn_id = str(created.turn_id)

    turn = store.claim(turn_id)
    assert turn is not None
    worker = ChatWorker(store)
    worker._project(turn, "STARTING", "worker_acquired")
    worker._project(turn, "RUNNING", "provider_started")

    # Simulate an in-flight/unknown TOOL effect and pause the turn for reconciliation.
    worker._effect_started(
        turn,
        kind="tool",
        invocation_ref="tool:call-write-1",
        request_ref="tool:write_file",
    )
    worker._journal.mark_unresolved_unknown(
        worker._journal_scope(turn),
        now=datetime.now(UTC),
    )

    assert worker.has_unresolved_tool_effects(turn) is True
    worker._project(turn, "PAUSED", "effect_reconciliation_required")
    store.pause_for_reconciliation(
        turn,
        code="PROVIDER_STREAM_INTERRUPTED",
        end_reason="idle_timeout",
        message="O modelo ficou sem responder por 180 s e o processo foi encerrado. O trabalho feito até aqui está salvo.",
    )

    # recover_stale must NOT auto-resume while the TOOL effect is unresolved
    assert worker.recover_stale(maximum_age=timedelta(minutes=30)) == ()

    # Call POST /v1/conversations/{conversation_id}/turns/{turn_id}/resume
    resumed_resp = admin.api.post(
        f"/v1/conversations/{conv_id}/turns/{turn_id}/resume",
        json={},
        headers=admin.headers(),
    )
    assert resumed_resp.status_code == 202, resumed_resp.text
    resumed_data = resumed_resp.json()
    assert resumed_data["resumed"] is True
    assert resumed_data["state"] == "queued"
    assert "tool:call-write-1" in resumed_data["acknowledged_effects"]
    assert worker.has_unresolved_tool_effects(turn) is False

    from sqlalchemy import select
    from agentos.persistence.postgres.schema import execution_effects
    with store._engine.connect() as conn:
        eff_rows = conn.execute(
            select(execution_effects.c.invocation_ref, execution_effects.c.state, execution_effects.c.reconciliation_ref)
            .where(execution_effects.c.execution_id == str(turn["execution_id"]))
        ).mappings().all()
    assert any(
        r["invocation_ref"] == "tool:call-write-1"
        and r["state"] == ExecutionEffectState.UNKNOWN_ACKED.value
        and r["reconciliation_ref"] is not None
        for r in eff_rows
    )

    # Now the worker can claim the resumed turn and complete it, seeing the unknown-tool verification note in history.
    claimed_again = store.claim(turn_id)
    assert claimed_again is not None
    history = store.history_for_turn(claimed_again)
    assert any(
        "tool:call-write-1" in str(m.get("content"))
        and "UNKNOWN_ACKED" in str(m.get("content"))
        and "reconhecidas pelo usuário" in str(m.get("content"))
        for m in history
    )
    worker._project(claimed_again, "STARTING", "worker_acquired")
    worker._project(claimed_again, "RUNNING", "provider_started")
    store.delta(claimed_again, "Tarefa retomada e concluída.")
    worker._project(
        claimed_again,
        "COMPLETED",
        "provider_completed",
        result_ref=f"conversation-message:{claimed_again['assistant_message_id']}",
    )
    store.finish(claimed_again)
    conv_view = store.get(conv_id, admin.user_id)
    assert conv_view["state"] == "completed"

    # A turn paused with PROVIDER_STREAM_INTERRUPTED and a clean tool ledger is auto-resumed by recover_stale.
    created2 = store.create(
        user_id=admin.user_id,
        message="Follow-up with clean ledger",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="resume-test-2",
    )
    conv2_id = str(created2.conversation_id)
    turn2_id = str(created2.turn_id)
    turn2 = store.claim(turn2_id)
    assert turn2 is not None
    worker._project(turn2, "STARTING", "worker_acquired")
    worker._project(turn2, "RUNNING", "provider_started")
    worker._project(turn2, "PAUSED", "effect_reconciliation_required")
    store.pause_for_reconciliation(
        turn2,
        code="PROVIDER_STREAM_INTERRUPTED",
        end_reason="stdout_eof",
        message="O processo do modelo terminou sem concluir a resposta.",
    )
    recovered_ids = worker.recover_stale(maximum_age=timedelta(minutes=30))
    assert turn2_id in recovered_ids
    assert store.get(conv2_id, admin.user_id)["state"] == "queued"


