"""A repeated read that nothing could have changed is served from the turn.

The runtime already short-circuits a repeated *failure*. A repeated success
was free, and the trilha A instrumentation named it: redundant_tool_calls.

The correctness hinge is the second condition. Re-reading a file after
editing it is not redundant, it is necessary, so a write anywhere between
the two reads makes the second one run for real.
"""
from __future__ import annotations

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
        "provider": "openrouter", "model_id": "model",
        "user_message_id": "user-message-1", "assistant_message_id": "assistant-message-1",
    }


class _Store:
    def load(self, turn_id: str): return _turn()
    def history_for_turn(self, turn): return [{"role": "user", "content": "trabalhe"}]
    def lifecycle(self, turn, state, **payload) -> None: ...
    def delta(self, turn, text) -> None: ...
    def finish(self, turn, *, failed: bool = False, code: str | None = None) -> None: ...


class _Toolset:
    """Counts real invocations per tool name."""

    READ_ONLY = {"read_file", "list_files", "search_files"}

    def __init__(self) -> None:
        self.invocations: list[tuple[str, str]] = []
        self.reads = 0

    def schemas(self, allowed=None, kinds=None): return []
    def is_read_only(self, name): return name in self.READ_ONLY
    def argument_names(self, name): return None

    def invoke(self, name, arguments):
        self.invocations.append((name, str(arguments)))
        if name in self.READ_ONLY:
            self.reads += 1
            return ToolOutcome("succeeded", "lido", f"conteudo #{self.reads}", {})
        return ToolOutcome("succeeded", "escrito", "ok", {})


class _Script:
    """Plays a fixed list of (tool, arguments) calls, then answers."""

    def __init__(self, calls: list[tuple[str, str]]) -> None:
        self.calls = calls
        self.index = 0

    def stream(self, request):
        if self.index >= len(self.calls):
            return normalize_sse([
                'data: {"choices":[{"delta":{"content":"pronto"},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ], provider="openrouter")
        name, arguments = self.calls[self.index]
        self.index += 1
        return normalize_sse([
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-%d","function":'
            '{"name":"%s","arguments":"%s"}}]},"finish_reason":"tool_calls"}]}'
            % (self.index, name, arguments.replace('"', '\\"')),
            "data: [DONE]",
        ], provider="openrouter")


def _run(calls: list[tuple[str, str]]) -> _Toolset:
    toolset = _Toolset()
    AgenticTurnRuntime(
        store=_Store(), provider=_Script(calls), toolset=toolset, system_prompt="regras",
        limits=AgenticLimits(max_iterations=None, max_actions=None, max_context_tokens=200_000),
    ).run("turn-1")
    return toolset


READ_A = ("read_file", '{"path": "a.txt"}')
READ_B = ("read_file", '{"path": "b.txt"}')
WRITE_A = ("write_file", '{"path": "a.txt", "content": "novo"}')
RUN = ("run_command", '{"command": "cat a.txt"}')


def test_an_identical_repeated_read_does_not_reach_the_tool() -> None:
    toolset = _run([READ_A, READ_A])
    assert toolset.invocations.count(("read_file", "{'path': 'a.txt'}")) == 1


def test_a_different_read_still_runs() -> None:
    toolset = _run([READ_A, READ_B])
    assert toolset.reads == 2


def test_a_write_in_between_makes_the_next_read_run_for_real() -> None:
    """The whole correctness of this feature is here."""
    toolset = _run([READ_A, WRITE_A, READ_A])
    assert toolset.reads == 2


def test_a_write_to_another_path_still_forces_the_re_read() -> None:
    """The runtime cannot know which file a tool touched, so any write counts."""
    toolset = _run([READ_A, ("write_file", '{"path": "outro.txt", "content": "x"}'), READ_A])
    assert toolset.reads == 2


def test_a_command_is_never_deduplicated() -> None:
    """run_command is not read-only; the runtime cannot know it only reads."""
    toolset = _run([RUN, RUN])
    assert toolset.invocations.count(("run_command", "{'command': 'cat a.txt'}")) == 2


def test_the_served_result_says_where_it_came_from() -> None:
    captured: list[str] = []

    class _Recording(_Store):
        def lifecycle(self, turn, state, **payload) -> None:
            if state == "tool_finished":
                captured.append(str(payload.get("summary") or ""))

    toolset = _Toolset()
    AgenticTurnRuntime(
        store=_Recording(), provider=_Script([READ_A, READ_A]), toolset=toolset, system_prompt="r",
        limits=AgenticLimits(max_iterations=None, max_actions=None, max_context_tokens=200_000),
    ).run("turn-1")

    assert any("anterior" in item for item in captured)


def test_the_repeated_read_still_gets_the_original_content() -> None:
    """Serving a pointer with no content would just force the model to ask again."""
    seen: list[str] = []

    class _Capturing(_Store):
        def lifecycle(self, turn, state, **payload) -> None: ...

    class _Watching(_Script):
        def stream(self, request):
            seen.append(str(request["messages"]))
            return super().stream(request)

    AgenticTurnRuntime(
        store=_Capturing(), provider=_Watching([READ_A, READ_A]), toolset=_Toolset(), system_prompt="r",
        limits=AgenticLimits(max_iterations=None, max_actions=None, max_context_tokens=200_000),
    ).run("turn-1")

    assert "conteudo #1" in seen[-1]


def test_a_repeated_read_counts_as_redundant_in_the_metrics() -> None:
    """The measurement must still see the model asking twice."""
    toolset = _Toolset()
    runtime = AgenticTurnRuntime(
        store=_Store(), provider=_Script([READ_A, READ_A]), toolset=toolset, system_prompt="r",
        limits=AgenticLimits(max_iterations=None, max_actions=None, max_context_tokens=200_000),
    )
    runtime.run("turn-1")

    assert runtime.counters.tool_calls == 2
    assert runtime.counters.redundant_tool_calls == 1
