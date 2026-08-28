from __future__ import annotations

from agentos.agentic.quality import TurnQualityCounters


def test_first_call_with_a_signature_is_not_redundant() -> None:
    counters = TurnQualityCounters()
    counters.note_call("read_file", {"path": "a.txt"}, "succeeded")
    assert counters.tool_calls == 1
    assert counters.redundant_tool_calls == 0


def test_repeating_a_successful_call_counts_as_redundant() -> None:
    counters = TurnQualityCounters()
    counters.note_call("read_file", {"path": "a.txt"}, "succeeded")
    counters.note_call("read_file", {"path": "a.txt"}, "succeeded")
    assert counters.tool_calls == 2
    assert counters.redundant_tool_calls == 1


def test_argument_order_does_not_create_a_new_signature() -> None:
    counters = TurnQualityCounters()
    counters.note_call("search_files", {"pattern": "x", "glob": "*.py"}, "succeeded")
    counters.note_call("search_files", {"glob": "*.py", "pattern": "x"}, "succeeded")
    assert counters.redundant_tool_calls == 1


def test_different_arguments_are_not_redundant() -> None:
    counters = TurnQualityCounters()
    counters.note_call("read_file", {"path": "a.txt"}, "succeeded")
    counters.note_call("read_file", {"path": "b.txt"}, "succeeded")
    assert counters.redundant_tool_calls == 0


def test_a_repeated_failure_is_not_counted_as_redundant() -> None:
    """The runtime already short-circuits a repeated failure via _failed_signatures.

    Counting it here would double-report a call the loop never actually made
    against the tool, inflating the very metric the trilha is judged by.
    """
    counters = TurnQualityCounters()
    counters.note_call("run_command", {"command": "nope"}, "failed")
    counters.note_call("run_command", {"command": "nope"}, "failed")
    assert counters.tool_calls == 2
    assert counters.redundant_tool_calls == 0


def test_a_call_that_succeeds_after_failing_is_not_redundant() -> None:
    counters = TurnQualityCounters()
    counters.note_call("run_command", {"command": "build"}, "failed")
    counters.note_call("run_command", {"command": "build"}, "succeeded")
    assert counters.redundant_tool_calls == 0


def test_unhashable_arguments_do_not_crash_the_counter() -> None:
    counters = TurnQualityCounters()
    counters.note_call("edit_file", {"edits": [{"old_text": "a", "new_text": "b"}]}, "succeeded")
    counters.note_call("edit_file", {"edits": [{"old_text": "a", "new_text": "b"}]}, "succeeded")
    assert counters.redundant_tool_calls == 1


def test_usage_keeps_the_latest_reported_value_per_field() -> None:
    """Input usage can arrive at the start of a call and output usage at the end.

    Each field has to survive an update that does not mention it, so the two
    halves still add up to one complete provider call.
    """
    counters = TurnQualityCounters()
    counters.note_usage(input_tokens=100, output_tokens=None, cached_input_tokens=None)
    counters.note_usage(input_tokens=None, output_tokens=40, cached_input_tokens=None)
    counters.settle_provider_call()
    assert counters.input_tokens == 100
    assert counters.output_tokens == 40


def test_usage_accumulates_across_provider_calls() -> None:
    counters = TurnQualityCounters()
    counters.note_usage(input_tokens=100, output_tokens=10, cached_input_tokens=None)
    counters.settle_provider_call()
    counters.note_usage(input_tokens=200, output_tokens=20, cached_input_tokens=None)
    counters.settle_provider_call()
    assert counters.input_tokens == 300
    assert counters.output_tokens == 30


def test_cached_input_tokens_stay_none_when_no_provider_reports_them() -> None:
    """None means "the provider never told us", which is not the same as zero."""
    counters = TurnQualityCounters()
    counters.note_usage(input_tokens=100, output_tokens=10, cached_input_tokens=None)
    counters.settle_provider_call()
    assert counters.cached_input_tokens is None


def test_cached_input_tokens_accumulate_once_reported() -> None:
    counters = TurnQualityCounters()
    counters.note_usage(input_tokens=100, output_tokens=10, cached_input_tokens=0)
    counters.settle_provider_call()
    counters.note_usage(input_tokens=100, output_tokens=10, cached_input_tokens=900)
    counters.settle_provider_call()
    assert counters.cached_input_tokens == 900


def test_usage_parser_reads_anthropic_cache_reads() -> None:
    from agentos.agentic.provider_stream import _usage

    parsed = _usage({"input_tokens": 12, "output_tokens": 3, "cache_read_input_tokens": 900})
    assert parsed.cached_input_tokens == 900


def test_usage_parser_reads_openai_cached_tokens() -> None:
    from agentos.agentic.provider_stream import _usage

    parsed = _usage({"prompt_tokens": 40, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 120}})
    assert parsed.cached_input_tokens == 120


def test_usage_parser_leaves_cache_unreported_as_none() -> None:
    from agentos.agentic.provider_stream import _usage

    parsed = _usage({"prompt_tokens": 40, "completion_tokens": 5})
    assert parsed.cached_input_tokens is None


# -- runtime wiring ---------------------------------------------------------


def _loop_turn() -> dict[str, object]:
    return {
        "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
        "provider": "openrouter", "model_id": "local-model",
        "user_message_id": "user-message-1", "assistant_message_id": "assistant-message-1",
    }


class _RecordingStore:
    def __init__(self) -> None:
        self.turn = _loop_turn()
        self.recorded: list[dict[str, object]] = []

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "read the file twice"}]

    def lifecycle(self, turn, state, **payload) -> None: ...
    def delta(self, turn, text) -> None: ...
    def finish(self, turn, *, failed: bool = False, code: str | None = None) -> None: ...

    def record_quality(self, turn, *, counters, outcome, error_code, duration_ms) -> None:
        self.recorded.append({"counters": dict(counters), "outcome": outcome, "error_code": error_code})


class _RepeatingProvider:
    """Calls read_file with identical arguments twice, then answers."""

    def __init__(self) -> None:
        self.calls = 0

    def stream(self, request):
        from agentos.agentic.provider_stream import normalize_sse

        self.calls += 1
        if self.calls <= 2:
            return normalize_sse([
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-%d","function":'
                '{"name":"read_file","arguments":"{\\"path\\":\\"a.txt\\"}"}}]},"finish_reason":"tool_calls"}]}' % self.calls,
                "data: [DONE]",
            ], provider="openrouter")
        return normalize_sse([
            'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ], provider="openrouter")


class _ReadToolset:
    def schemas(self): return []
    def is_read_only(self, name): return True
    def argument_names(self, name): return frozenset({"path"})

    def invoke(self, name, arguments):
        from agentos.agentic.agent_tools import ToolOutcome

        return ToolOutcome("succeeded", "lido", "conteudo do arquivo", {})


def test_the_runtime_reports_a_repeated_read_as_redundant() -> None:
    from agentos.agentic.runtime import AgenticTurnRuntime

    store = _RecordingStore()
    result = AgenticTurnRuntime(store=store, provider=_RepeatingProvider(), toolset=_ReadToolset()).run("turn-1")

    assert result.state == "completed"
    assert len(store.recorded) == 1
    counters = store.recorded[0]["counters"]
    assert store.recorded[0]["outcome"] == "completed"
    assert counters["tool_calls"] == 2
    assert counters["redundant_tool_calls"] == 1
    assert counters["iterations"] == 3


def test_a_store_without_the_recorder_still_completes_the_turn() -> None:
    """Every in-memory double lacks record_quality; that must stay harmless."""
    from agentos.agentic.runtime import AgenticTurnRuntime

    class _Bare(_RecordingStore):
        record_quality = None

    store = _Bare()
    result = AgenticTurnRuntime(store=store, provider=_RepeatingProvider(), toolset=_ReadToolset()).run("turn-1")
    assert result.state == "completed"
    assert store.recorded == []
