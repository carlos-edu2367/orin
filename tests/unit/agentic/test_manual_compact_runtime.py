from __future__ import annotations

from agentos.agentic.compaction import COMPACTION_SECTIONS
from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime


class Store:
    def __init__(self, history: list[dict]) -> None:
        self.turn = {
            "turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e", "provider": "openrouter",
            "model_id": "m", "user_message_id": "um", "assistant_message_id": "am",
        }
        self.history = history
        self.deltas: list[str] = []
        self.boundaries: list[dict] = []
        self.marked = 0
        self.finished: list[str | None] = []

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return list(self.history)

    def lifecycle(self, turn, state, **payload):
        pass

    def delta(self, turn, text):
        self.deltas.append(text)

    def finish(self, turn, *, failed=False, code=None):
        self.finished.append(code)

    def mark_command_turn(self, turn):
        self.marked += 1

    def record_compact_boundary(self, turn, **values):
        self.boundaries.append(dict(values))


class Summarizer:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def stream(self, request):
        self.requests.append(request)
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text="\n".join(f"### {s}\n- d" for s in COMPACTION_SECTIONS))])


# Stored sequences, as the real store attaches them: a compaction records the sequence it folded.
HISTORY = [
    {"role": "user", "content": "build the API", "_sequence": 1}, {"role": "assistant", "content": "done " + "x" * 400, "_sequence": 2},
    {"role": "user", "content": "add tests", "_sequence": 3}, {"role": "assistant", "content": "added " + "y" * 400, "_sequence": 4},
]


def test_a_manual_compaction_summarizes_everything_with_the_instructions() -> None:
    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    store = Store(HISTORY)
    provider = Summarizer()
    result = AgenticTurnRuntime(store=store, provider=provider, lifecycle=bus, compact_request="keep the API names",
                                limits=AgenticLimits()).run("t")
    assert result.state == "completed" and store.marked == 1
    assert store.boundaries and store.boundaries[0]["trigger"] == "manual"
    pre = next(dict(event.payload) for event in events if event.name is HookEvent.PRE_COMPACT)
    assert pre["trigger"] == "manual" and pre["custom_instructions"] == "keep the API names"
    assert "keep the API names" in str(provider.requests[0]["messages"][-1]["content"])
    assert any("Conversa compactada" in text for text in store.deltas) and store.finished == [None]


def test_a_hook_can_block_a_manual_compaction() -> None:
    bus = LifecycleBus()
    bus.subscribe(lambda event: LifecycleDecision(blocked=True, reason="not now") if event.name is HookEvent.PRE_COMPACT else None)
    store = Store(HISTORY)
    result = AgenticTurnRuntime(store=store, provider=Summarizer(), lifecycle=bus, compact_request="").run("t")
    assert result.state == "completed" and store.boundaries == []
    assert any("bloqueada" in text and "not now" in text for text in store.deltas)


def test_nothing_to_compact() -> None:
    store = Store([])
    result = AgenticTurnRuntime(store=store, provider=Summarizer(), compact_request="").run("t")
    assert result.error_code == "NOTHING_TO_COMPACT" and any("Ainda não há" in text for text in store.deltas)


def test_the_session_turns_a_compact_turn_into_a_compaction_request(tmp_path) -> None:
    from agentos.agentic.session import TurnSession

    class SessionStore(Store):
        def record(self, *args, **kwargs):
            pass

        def main_agent_id(self, turn):
            return "agent:c:main"

    class Agents:
        def list(self):
            return []

    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    store = SessionStore([{"role": "user", "content": "/compact focus on API"}])
    session = TurnSession(turn={**store.turn, "turn_kind": "compact"}, store=store, agents_store=Agents(), memory_store=None,
                          provider_factory=lambda: Summarizer(), workspace_root=tmp_path, enable_subagents=False, lifecycle=bus)
    runtime = session.build_runtime()
    assert runtime.compact_request == "focus on API"
    assert HookEvent.USER_PROMPT_SUBMIT not in [event.name for event in events]
