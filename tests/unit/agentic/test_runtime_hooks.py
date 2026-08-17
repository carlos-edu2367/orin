from __future__ import annotations

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime


def _turn():
    return {"turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1"}


class RecordingEngine:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def dispatch(self, *, user_id, event, payload):
        self.calls.append((event, dict(payload)))
        return ()


class LookupToolset:
    def is_read_only(self, name):
        return False

    def argument_names(self, name):
        return frozenset({"q"})

    def invoke(self, name, arguments):
        return ToolOutcome("succeeded", "ok", "done", {})


def test_post_tool_use_dispatches_with_the_tool_name():
    engine = RecordingEngine()
    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=LookupToolset(), hook_engine=engine)

    runtime._failed_signatures = {}
    runtime._run_toolset(_turn(), [{"id": "call-1", "name": "lookup", "arguments": '{"q": "x"}'}])

    assert engine.calls == [("PostToolUse", {"tool_name": "lookup", "status": "succeeded"})]


def test_post_compact_dispatches_after_compaction():
    engine = RecordingEngine()
    runtime = AgenticTurnRuntime(
        store=object(), provider=object(), hook_engine=engine,
        limits=AgenticLimits(max_context_tokens=1_000),
    )
    messages = [{"role": "user", "content": f"old request {index} " + "x" * 500} for index in range(8)]
    messages.append({"role": "user", "content": "current request"})
    runtime._pinned_index = len(messages) - 1

    runtime._maybe_compact(messages, _turn(), [])

    assert [event for event, _payload in engine.calls] == ["PostCompact"]
    assert engine.calls[0][1]["compaction_count"] == 1


def test_a_hook_that_fails_does_not_fail_the_tool_result():
    class Exploding:
        def dispatch(self, **_kwargs):
            raise RuntimeError("boom")

    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=LookupToolset(), hook_engine=Exploding())

    runtime._failed_signatures = {}
    results = runtime._run_toolset(_turn(), [{"id": "call-1", "name": "lookup", "arguments": '{"q": "x"}'}])

    assert results[0]["status"] == "succeeded"


def test_no_hook_engine_is_a_silent_no_op():
    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=LookupToolset())

    runtime._failed_signatures = {}
    results = runtime._run_toolset(_turn(), [{"id": "call-1", "name": "lookup", "arguments": '{"q": "x"}'}])

    assert results[0]["status"] == "succeeded"
