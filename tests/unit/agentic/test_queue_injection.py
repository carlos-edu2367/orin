from __future__ import annotations

from datetime import timedelta

from agentos.agentic import transcript
from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime


class Store:
    def __init__(self, queued: list[dict]) -> None:
        self.turn = {
            "turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e",
            "provider": "openrouter", "model_id": "m", "user_message_id": "um", "assistant_message_id": "am",
        }
        self.queued = list(queued)
        self.blocked: list[str] = []
        self.steps: list[tuple[str, dict]] = []
        self.deltas: list[str] = []

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "start"}]

    def lifecycle(self, turn, state, **payload):
        pass

    def delta(self, turn, text):
        self.deltas.append(text)

    def finish(self, turn, *, failed=False, code=None):
        pass

    def record_step(self, turn, *, kind, payload, **fields):
        self.steps.append((kind, dict(payload)))

    def claim_queued_inputs(self, turn):
        items, self.queued = self.queued, []
        return items

    def mark_queued_input_blocked(self, queue_id):
        self.blocked.append(queue_id)


class ToolThenText:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return iter([
                NormalizedStreamItem(StreamKind.TOOL_CALL, 1, tool_call_id="c1", tool_name="read_file", arguments_delta='{"path": "a"}'),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
            ])
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text="done"), NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")])


class Toolset:
    def schemas(self, allowed=None):
        return []

    def is_read_only(self, name):
        return True

    def argument_names(self, name):
        return frozenset({"path"})

    def invoke(self, name, arguments):
        return ToolOutcome("succeeded", "ok", "content")


def _runtime(store, provider, bus=None):
    return AgenticTurnRuntime(
        store=store, provider=provider, toolset=Toolset(), lifecycle=bus,
        limits=AgenticLimits(max_iterations=None, max_actions=None, deadline=timedelta(hours=1)),
    )


def test_queued_input_reaches_the_model_after_the_tool_round() -> None:
    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    store = Store([{"queue_id": "q1", "content": "also do X"}])
    provider = ToolThenText()
    result = _runtime(store, provider, bus).run("t")
    assert result.state == "completed" and result.queued_turn_count == 1
    assert {"role": "user", "content": "also do X"} in provider.requests[1]["messages"]
    assert ("queued_input", {"content": "also do X"}) in store.steps
    prompts = [dict(event.payload)["prompt"] for event in events if event.name is HookEvent.USER_PROMPT_SUBMIT]
    assert prompts == ["also do X"]


def test_a_hook_can_block_queued_input() -> None:
    bus = LifecycleBus()
    bus.subscribe(lambda event: LifecycleDecision(blocked=True, reason="nope") if event.name is HookEvent.USER_PROMPT_SUBMIT else None)
    store = Store([{"queue_id": "q1", "content": "rm -rf everything"}])
    provider = ToolThenText()
    result = _runtime(store, provider, bus).run("t")
    assert result.state == "completed" and result.queued_turn_count == 0
    assert store.blocked == ["q1"]
    assert not any("rm -rf" in str(m.get("content")) for m in provider.requests[1]["messages"])
    assert any("bloqueada" in text for text in store.deltas)


def test_a_queued_input_step_replays_as_a_user_message() -> None:
    steps = [
        {"kind": transcript.STEP_ASSISTANT_TOOL_CALL, "payload": {"text": "", "calls": [{"id": "c1", "name": "read_file", "arguments": "{}"}]}},
        {"kind": transcript.STEP_TOOL_RESULT, "payload": {"id": "c1", "name": "read_file", "status": "succeeded", "content": "x"}},
        {"kind": transcript.STEP_QUEUED_INPUT, "payload": {"content": "also do X"}},
    ]
    projected = transcript.project(steps, "openrouter")
    assert projected[-1] == {"role": "user", "content": "also do X"}
    assert transcript.STEP_QUEUED_INPUT in transcript.STEP_KINDS
