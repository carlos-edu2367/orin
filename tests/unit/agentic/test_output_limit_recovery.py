from __future__ import annotations

from datetime import timedelta

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind, normalize_sse
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.providers.models import FinishReason


class Store:
    def __init__(self, provider: str = "openrouter") -> None:
        self.turn = {
            "turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e",
            "provider": provider, "model_id": "m", "user_message_id": "um", "assistant_message_id": "am",
        }
        self.deltas: list[str] = []

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "write it"}]

    def lifecycle(self, turn, state, **payload):
        pass

    def delta(self, turn, text):
        self.deltas.append(text)

    def finish(self, turn, *, failed=False, code=None):
        pass


class Scripted:
    def __init__(self, responses: list[list[NormalizedStreamItem]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def stream(self, request):
        self.requests.append(request)
        return iter(self.responses.pop(0))


def _text(text: str, finish: str) -> list[NormalizedStreamItem]:
    return [NormalizedStreamItem(StreamKind.TEXT, 1, text=text), NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason=finish)]


def _call(call_id: str, arguments: str, finish: str) -> list[NormalizedStreamItem]:
    return [
        NormalizedStreamItem(StreamKind.TOOL_CALL, 1, tool_call_id=call_id, tool_name="write_file", arguments_delta=arguments),
        NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason=finish),
    ]


class Toolset:
    def __init__(self) -> None:
        self.invoked: list[dict] = []

    def schemas(self, allowed=None):
        return []

    def is_read_only(self, name):
        return False

    def argument_names(self, name):
        return frozenset({"path", "content"})

    def invoke(self, name, arguments):
        self.invoked.append(dict(arguments))
        return ToolOutcome("succeeded", "ok", "written")


def _run(provider, store=None, toolset=None, **limits):
    store = store or Store()
    toolset = toolset or Toolset()
    runtime = AgenticTurnRuntime(
        store=store, provider=provider, toolset=toolset,
        limits=AgenticLimits(max_iterations=None, max_actions=None, deadline=timedelta(hours=1), **limits),
    )
    return runtime.run("t"), store, toolset


def test_anthropic_max_tokens_is_a_length_finish() -> None:
    items = list(normalize_sse(['data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"}}'], provider="anthropic"))
    assert [item.finish_reason for item in items if item.kind is StreamKind.FINISH] == [FinishReason.LENGTH]


def test_a_cut_answer_is_continued_without_a_paragraph_break() -> None:
    provider = Scripted([_text("Part one", "length"), _text(" and part two.", "stop")])
    result, store, _ = _run(provider)
    assert result.state == "completed"
    assert store.deltas == ["Part one", " and part two."]
    second = provider.requests[1]["messages"]
    assert any(m.get("role") == "assistant" and m.get("content") == "Part one" for m in second)
    assert "cut off by the output length limit" in str(second[-1]["content"])


def test_continuations_stop_after_three() -> None:
    provider = Scripted([_text(f"chunk {n}", "length") for n in range(4)])
    result, _, _ = _run(provider)
    assert result.state == "completed" and len(provider.requests) == 4


def test_a_cut_tool_call_is_never_run_and_the_cap_is_raised_for_anthropic() -> None:
    provider = Scripted([
        _call("c1", '{"path": "a.txt", "content": "abc', "length"),
        _call("c2", '{"path": "a.txt", "content": "abc"}', "tool_calls"),
        _text("done", "stop"),
    ])
    result, _, toolset = _run(provider, store=Store(provider="anthropic"))
    assert result.state == "completed"
    assert toolset.invoked == [{"path": "a.txt", "content": "abc"}]
    assert provider.requests[0]["max_output_tokens"] is None
    assert provider.requests[1]["max_output_tokens"] == 16_384
    assert "was not run" in str(provider.requests[1]["messages"][-1]["content"])


def test_without_a_provider_cap_nothing_is_raised() -> None:
    provider = Scripted([_call("c1", '{"path": "a.txt", "content": "ab', "length"), _text("ok", "stop")])
    result, _, toolset = _run(provider)
    assert result.state == "completed" and toolset.invoked == []
    assert provider.requests[1]["max_output_tokens"] is None
    assert "is now" not in str(provider.requests[1]["messages"][-1]["content"])


def test_an_explicit_cap_is_never_raised() -> None:
    provider = Scripted([_call("c1", '{"path": "a', "length"), _text("ok", "stop")])
    _run(provider, store=Store(provider="anthropic"), max_output_tokens=4_000)
    assert provider.requests[1]["max_output_tokens"] == 4_000


def test_a_complete_call_with_a_length_finish_still_runs() -> None:
    provider = Scripted([_call("c1", '{"path": "a.txt", "content": "abc"}', "length"), _text("ok", "stop")])
    result, _, toolset = _run(provider)
    assert result.state == "completed" and toolset.invoked == [{"path": "a.txt", "content": "abc"}]
