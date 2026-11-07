"""Phase 1 of specialised agents: a subagent may start its own subagents (a tree, not a flat list)."""
from __future__ import annotations

import threading
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any

import pytest

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.session import SubagentCaller, TurnSession, _SubagentSlot
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN,
    _parallel_tool_calls_batch,
    _RecordingStore,
    _text_batch,
)

DELEGATION_TOOLS = {"run_subagent", "Agent"}
MAIN_ID = f"agent:{TURN['conversation_id']}:main"


class _TreeStore(_RecordingStore):
    """Also keeps the parent of every event."""

    def __init__(self) -> None:
        super().__init__()
        self.tree_events: list[tuple[AgentActivityEventType, str | None, str | None, dict[str, Any]]] = []

    def record(self, turn, event_type, summary, payload=None, *, agent_id=None, parent_agent_id=None) -> None:
        super().record(turn, event_type, summary, payload, agent_id=agent_id, parent_agent_id=parent_agent_id)
        with self._lock:
            self.tree_events.append((event_type, agent_id, parent_agent_id, dict(payload or {})))


def _tool_names(request: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in request.get("tools") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or (item.get("function") or {}).get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def _delegate(task: str, subagent_type: str = "general", call_id: str = "call") -> list:
    return _parallel_tool_calls_batch([(call_id, "run_subagent", {"subagent_type": subagent_type, "task": task})])


class _Scripted:
    """One provider for every subagent: the calls are sequential, so a queue of answers is deterministic."""

    def __init__(self, batches: list[list]) -> None:
        self.batches = list(batches)
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def stream(self, request: dict[str, Any]):
        with self.lock:
            self.requests.append(request)
            return iter(self.batches.pop(0))


@pytest.fixture()
def agents_store(tmp_path: Path):
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'tree.db').as_posix()}")
    metadata.create_all(engine)
    yield ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    engine.dispose()


def _session(tmp_path: Path, store, agents_store, main: _Scripted, child: _Scripted, **kwargs: Any) -> TurnSession:
    return TurnSession(
        turn=dict(TURN), store=store, agents_store=agents_store, memory_store=None,
        provider_factory=lambda: main, workspace_root=tmp_path, child_provider_factory=lambda _m: child, **kwargs,
    )


@pytest.mark.parametrize("max_concurrent", [1, 4])
def test_three_levels_of_subagents_run_with_their_real_parents_and_the_last_level_cannot_delegate(
    tmp_path: Path, agents_store, max_concurrent: int,
) -> None:
    store = _TreeStore()
    main = _Scripted([_delegate("nivel 1"), _text_batch("Principal: tudo pronto.")])
    child = _Scripted([
        _delegate("nivel 2"),          # level 1 delegates
        _delegate("nivel 3"),          # level 2 delegates
        _text_batch("folha pronta"),   # level 3 (the deepest) answers
        _text_batch("nivel 2 pronto"),
        _text_batch("nivel 1 pronto"),
    ])
    session = _session(tmp_path, store, agents_store, main, child, max_concurrent_subagents=max_concurrent)

    outcome: dict[str, Any] = {}

    def run() -> None:
        runtime = session.build_runtime()
        try:
            outcome["result"] = runtime.run(TURN["turn_id"])
        finally:
            runtime.close()

    # With a single concurrency slot a waiting parent must lend its slot, or this chain never finishes.
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive(), "nested delegation deadlocked"
    assert outcome["result"].state == "completed"

    assert DELEGATION_TOOLS & _tool_names(child.requests[0]), "level 1 can delegate"
    assert DELEGATION_TOOLS & _tool_names(child.requests[1]), "level 2 can delegate"
    assert not DELEGATION_TOOLS & _tool_names(child.requests[2]), "level 3 is the deepest and cannot delegate"

    rows = {str(row["name"]): row for row in agents_store.list()}
    level1, level2, level3 = rows["general"], rows["general-2"], rows["general-3"]
    assert level1["parent_agent_id"] == MAIN_ID
    assert level2["parent_agent_id"] == level1["agent_id"]
    assert level3["parent_agent_id"] == level2["agent_id"]

    expected_parent = {
        MAIN_ID: None,
        level1["agent_id"]: MAIN_ID,
        level2["agent_id"]: level1["agent_id"],
        level3["agent_id"]: level2["agent_id"],
    }
    seen_with_parent: set[str] = set()
    for event_type, agent_id, parent_agent_id, _payload in store.tree_events:
        # Some lifecycle notices (tool.requested, context) never carried a parent; every one that does names the real one.
        if agent_id in expected_parent and parent_agent_id is not None:
            assert parent_agent_id == expected_parent[agent_id], (event_type, agent_id, parent_agent_id)
            seen_with_parent.add(agent_id)
    assert seen_with_parent == {level1["agent_id"], level2["agent_id"], level3["agent_id"]}

    sent = [(agent_id, payload) for event_type, agent_id, _p, payload in store.tree_events if event_type == AgentActivityEventType.AGENT_MESSAGE_SENT]
    assert [agent_id for agent_id, _ in sent] == [MAIN_ID, level1["agent_id"], level2["agent_id"]]
    assert [payload["sender_name"] for _, payload in sent] == ["Main", "general", "general-2"]
    received = [payload for event_type, agent_id, _p, payload in store.tree_events
                if event_type == AgentActivityEventType.AGENT_MESSAGE_RECEIVED and agent_id == level3["agent_id"]]
    assert received and received[0]["recipient_name"] == "general-2"


def test_a_subagent_at_the_depth_limit_is_refused(tmp_path: Path, agents_store) -> None:
    session = _session(tmp_path, _TreeStore(), agents_store, _Scripted([]), _Scripted([]))
    deepest = SubagentCaller(agent_id="agent:deep", name="deep", depth=3, permission_mode="accept_edits")
    outcome = session._run_subagent("general", "mais um nivel", caller=deepest)
    assert outcome.status == "failed"
    assert outcome.error_code == "SUBAGENT_DEPTH_LIMIT"


def test_subagent_max_depth_one_keeps_delegation_with_the_main_agent(tmp_path: Path, agents_store) -> None:
    main = _Scripted([_delegate("so um nivel"), _text_batch("ok")])
    child = _Scripted([_text_batch("feito")])
    session = _session(tmp_path, _TreeStore(), agents_store, main, child, subagent_max_depth=1)
    runtime = session.build_runtime()
    try:
        assert runtime.run(TURN["turn_id"]).state == "completed"
    finally:
        runtime.close()
    assert DELEGATION_TOOLS & _tool_names(main.requests[0])
    assert not DELEGATION_TOOLS & _tool_names(child.requests[0])


def test_read_only_subagents_get_no_delegation_tools(tmp_path: Path, agents_store) -> None:
    main = _Scripted([_delegate("procure", subagent_type="explore"), _text_batch("ok")])
    child = _Scripted([_text_batch("achei")])
    session = _session(tmp_path, _TreeStore(), agents_store, main, child)
    runtime = session.build_runtime()
    try:
        assert runtime.run(TURN["turn_id"]).state == "completed"
    finally:
        runtime.close()
    assert not DELEGATION_TOOLS & _tool_names(child.requests[0])


def _record_toolsets(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    builds: list[dict[str, Any]] = []
    original = TurnSession._toolset

    def recording(self, **kwargs):
        toolset = original(self, **kwargs)
        builds.append({**kwargs, "toolset": toolset})
        return toolset

    monkeypatch.setattr(TurnSession, "_toolset", recording)
    return builds


def test_a_grandchild_never_gets_more_permission_than_the_subagent_that_started_it(
    tmp_path: Path, agents_store, monkeypatch: pytest.MonkeyPatch,
) -> None:
    builds = _record_toolsets(monkeypatch)
    session = _session(tmp_path, _TreeStore(), agents_store, _Scripted([]), _Scripted([_text_batch("ok"), _text_batch("ok")]))
    session.permission_mode = "full_autonomy"
    planner = SubagentCaller(agent_id="agent:planner", name="planner", depth=1, permission_mode="plan_mode")
    assert session._run_subagent("general", "tarefa", caller=planner).status == "succeeded"
    assert builds[-1]["permission_mode"] == "plan_mode"

    reader = SubagentCaller(agent_id="agent:reader", name="reader", depth=1, permission_mode="accept_edits", read_only=True)
    assert session._run_subagent("general", "tarefa", caller=reader).status == "succeeded"
    assert builds[-1]["read_only_mode"] is True
    assert builds[-1]["permission_mode"] == "plan_mode"
    assert builds[-1]["subagents"] is False, "a read-only caller's children cannot delegate either"


def test_a_subagent_only_reaches_its_own_descendants(tmp_path: Path, agents_store) -> None:
    session = _session(tmp_path, _TreeStore(), agents_store, _Scripted([]), _Scripted([]))
    session._agent_parents.update({"agent:a1": MAIN_ID, "agent:a2": "agent:a1", "agent:b1": MAIN_ID})
    a1 = SubagentCaller(agent_id="agent:a1", name="a1", depth=1, permission_mode="accept_edits")

    assert session._check_subagent("agent:b1", caller=a1).error_code == "AGENT_NOT_OWNED"
    assert session._stop_background_subagent("agent:b1", caller=a1).error_code == "AGENT_NOT_OWNED"
    assert session._run_subagent("general", "retome", agent_id="agent:b1", caller=a1).error_code == "AGENT_NOT_OWNED"
    assert session._check_subagent("agent:a1", caller=a1).error_code == "AGENT_NOT_OWNED", "not even itself"
    # Its own descendant (and anything, for the main agent) is merely not running in the background here.
    assert session._check_subagent("agent:a2", caller=a1).error_code == "AGENT_NOT_FOUND"
    assert session._check_subagent("agent:b1").error_code == "AGENT_NOT_FOUND"


def test_teammates_stay_with_the_main_agent(tmp_path: Path, agents_store) -> None:
    session = _session(tmp_path, _TreeStore(), agents_store, _Scripted([]), _Scripted([]))
    child = SubagentCaller(agent_id="agent:a1", name="a1", depth=1, permission_mode="accept_edits")
    outcome = session._run_subagent("general", "trabalhe", teammate_name="ana", caller=child)
    assert outcome.error_code == "TEAMMATE_NOT_ALLOWED"


def test_stopping_a_subagent_stops_everything_it_started(tmp_path: Path, agents_store) -> None:
    session = _session(tmp_path, _TreeStore(), agents_store, _Scripted([]), _Scripted([]))
    session.cancelled = lambda _turn: False
    stopped = threading.Event()
    child = SubagentCaller(
        agent_id="agent:a1", name="a1", depth=1, permission_mode="accept_edits", cancelled=lambda _turn: stopped.is_set(),
    )
    own_stop = threading.Event()
    grandchild_cancelled = session._child_cancelled(own_stop, child)
    assert grandchild_cancelled(session.turn) is False
    stopped.set()
    assert grandchild_cancelled(session.turn) is True
    assert session._child_cancelled(None, child)(session.turn) is True

    outcome = session._run_subagent("general", "tarde demais", caller=child)
    assert outcome.error_code == "TURN_CANCELLED"


def test_a_nested_fork_inherits_the_conversation_of_the_subagent_that_started_it(tmp_path: Path, agents_store) -> None:
    child = _Scripted([_text_batch("fork pronto")])
    session = _session(tmp_path, _TreeStore(), agents_store, _Scripted([]), child)
    caller = SubagentCaller(
        agent_id="agent:a1", name="a1", depth=1, permission_mode="accept_edits",
        history=lambda: [{"role": "user", "content": "CALLER-ONLY-HISTORY"}],
    )
    assert session._run_subagent("fork", "continue", caller=caller).status == "succeeded"
    sent = repr(child.requests[0]["messages"])
    assert "CALLER-ONLY-HISTORY" in sent
    assert "Investigue o projeto em paralelo." not in sent


def test_a_waiting_subagent_lends_its_slot_and_takes_it_back() -> None:
    semaphore = BoundedSemaphore(1)
    slot = _SubagentSlot(semaphore)
    slot.acquire()
    assert not semaphore.acquire(blocking=False)
    with slot.lent():
        with slot.lent():  # two tool calls of the same subagent waiting at once
            assert semaphore.acquire(timeout=1)
            semaphore.release()
        assert not slot.held
    assert slot.held
    assert not semaphore.acquire(blocking=False)
    slot.release()
    assert semaphore.acquire(blocking=False)
    semaphore.release()


def test_the_store_saves_a_new_parent(agents_store) -> None:
    record = agents_store.create("revisor", "Revisa", parent_agent_id=MAIN_ID)
    agents_store.set_parent(str(record["agent_id"]), "agent:a1")
    assert agents_store.get(str(record["agent_id"]))["parent_agent_id"] == "agent:a1"


def test_a_slot_given_back_while_a_tool_still_waits_is_never_taken_again() -> None:
    semaphore = BoundedSemaphore(1)
    slot = _SubagentSlot(semaphore)
    slot.acquire()
    waiting = slot.lent()
    waiting.__enter__()  # a tool call of the run is waiting for its child
    slot.release()  # the run ends (deadline, cancel) before that call returns
    waiting.__exit__(None, None, None)
    assert not slot.held
    assert semaphore.acquire(blocking=False), "the place went back to the turn"
    semaphore.release()


def test_stopping_a_finished_background_agent_still_stops_what_it_started(tmp_path: Path, agents_store) -> None:
    from concurrent.futures import Future

    session = _session(tmp_path, _TreeStore(), agents_store, _Scripted([]), _Scripted([]))
    done: Future = Future()
    done.set_result(None)
    cancel = threading.Event()
    session._background_subagents["agent:a1"] = {"future": done, "agent_id": "agent:a1", "agent_name": "a1", "cancel": cancel}
    grandchild_cancelled = session._child_cancelled(
        threading.Event(),
        SubagentCaller(agent_id="agent:a1", name="a1", depth=1, permission_mode="accept_edits",
                       cancelled=session._child_cancelled(cancel)),
    )
    assert grandchild_cancelled(session.turn) is False
    assert session._stop_background_subagent("agent:a1").status == "succeeded"
    assert grandchild_cancelled(session.turn) is True


@pytest.mark.parametrize(("text", "names"), [
    ("use o @revisor.", ["revisor"]),
    ("@revisor: revise", ["revisor"]),
    ("abra @main.py e @c:/x e dev@x.com", []),
])
def test_agent_mentions_allow_punctuation_but_not_paths(text: str, names: list[str]) -> None:
    from agentos.agentic.session import _AGENT_MENTION

    assert [match.group(1) for match in _AGENT_MENTION.finditer(text)] == names
