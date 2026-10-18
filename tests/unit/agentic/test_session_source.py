from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.agentic.session import TurnSession, reset_process_sessions
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata


class Store:
    def __init__(self) -> None:
        self.turn = {"turn_id": "t", "conversation_id": "conv-source", "user_id": "u", "execution_id": "e", "provider": "openrouter",
                     "model_id": "m", "user_message_id": "um", "assistant_message_id": "am"}
        self.sessions: dict[str, dict] = {}

    def load(self, turn_id): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "one"}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "two"}]
    def lifecycle(self, turn, state, **payload): pass
    def delta(self, turn, text): pass
    def finish(self, turn, *, failed=False, code=None): pass
    def record(self, *args, **kwargs): pass
    def main_agent_id(self, turn): return "agent:c:main"
    def session_state(self, conversation_id): return self.sessions.get(conversation_id)

    def record_session_start(self, conversation_id, *, user_id, source):
        state = self.sessions.setdefault(conversation_id, {"start_count": 0})
        state.update(last_source=source, start_count=state["start_count"] + 1)


class Agents:
    def list(self): return []


def _starts(tmp_path: Path, store: Store, bus: LifecycleBus | None = None) -> list[str]:
    bus = bus or LifecycleBus()
    events = []
    bus.subscribe(events.append)
    TurnSession(turn=dict(store.turn), store=store, agents_store=Agents(), memory_store=None,
                provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False, lifecycle=bus).build_runtime()
    return [dict(event.payload)["source"] for event in events if event.name is HookEvent.SESSION_START]


def test_startup_once_then_resume_after_a_restart(tmp_path: Path) -> None:
    reset_process_sessions()
    store = Store()
    assert _starts(tmp_path, store) == ["startup"]  # history has two user messages: the store decides, not a count
    assert _starts(tmp_path, store) == []
    reset_process_sessions()  # a new worker process
    assert _starts(tmp_path, store) == ["resume"]
    assert store.sessions["conv-source"]["start_count"] == 2


def test_a_blocked_first_prompt_does_not_start_the_session_twice(tmp_path: Path) -> None:
    reset_process_sessions()
    store = Store()
    blocking = LifecycleBus()
    blocking.subscribe(lambda event: LifecycleDecision(blocked=True) if event.name is HookEvent.USER_PROMPT_SUBMIT else None)
    assert _starts(tmp_path, store, blocking) == ["startup"]
    assert _starts(tmp_path, store) == []


def test_store_round_trip() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    assert store.session_state("c") is None
    store.record_session_start("c", user_id="u", source="startup")
    store.record_session_start("c", user_id="u", source="resume")
    state = store.session_state("c")
    assert state is not None and state["last_source"] == "resume" and state["start_count"] == 2
