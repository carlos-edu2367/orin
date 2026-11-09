"""Cena Phase 3: per-subagent user stop and direct user message controls in TurnSession."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from agentos.agentic.background_agents import BACKGROUND_AGENTS
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.reminders import ReminderQueue
from agentos.agentic.session import TurnSession, _AgentControlWatcher
from agentos.conversations.agent_controls import AgentControlStore
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import conversation_agent_controls, metadata
from agentos.persistence.sqlite import create_local_engine
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN,
    _parallel_tool_calls_batch,
    _RecordingStore,
    _text_batch,
)


class _FakeClock:
    def __init__(self, initial: float = 100.0) -> None:
        self.now = initial
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.now

    def advance(self, seconds: float) -> float:
        with self._lock:
            self.now += seconds
            return self.now


class _ActivityStore(_RecordingStore):
    def __init__(self) -> None:
        super().__init__()
        self.activity_events: list[tuple[AgentActivityEventType, str, str | None, dict[str, Any]]] = []

    def record(self, turn, event_type, summary, payload=None, *, agent_id=None, parent_agent_id=None) -> None:
        super().record(turn, event_type, summary, payload, agent_id=agent_id, parent_agent_id=parent_agent_id)
        with self._lock:
            self.activity_events.append((event_type, str(summary), agent_id, dict(payload or {})))


def _delegate(task: str, subagent_type: str = "general", call_id: str = "call-1") -> list:
    return _parallel_tool_calls_batch([(call_id, "run_subagent", {"subagent_type": subagent_type, "task": task})])


def _make_env(tmp_path: Path, main_provider: Any, child_provider: Any):
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'subagent_controls.db').as_posix()}")
    metadata.create_all(engine)
    store = _ActivityStore()
    chat_store = PostgresChatStore(engine)
    agents_store = ConversationAgentStore(
        engine,
        conversation_id=TURN["conversation_id"],
        user_id=TURN["user_id"],
    )
    clock = _FakeClock()
    session = TurnSession(
        turn=dict(TURN),
        store=store,
        chat_store=chat_store,
        agents_store=agents_store,
        memory_store=None,
        provider_factory=lambda: main_provider,
        workspace_root=tmp_path,
        child_provider_factory=lambda _m: child_provider,
    )
    session._agent_control_clock = clock
    return engine, store, agents_store, session, clock


def test_stop_recorded_during_synchronous_subagent_stops_it_and_main_turn_continues(
    tmp_path: Path,
) -> None:
    """Case a): a stop recorded during a synchronous subagent stops it with STOPPED_BY_USER and the main turn continues."""
    engine_holder: list[Any] = []
    session_holder: list[TurnSession] = []
    clock_holder: list[_FakeClock] = []
    agents_holder: list[ConversationAgentStore] = []

    class _MainProvider:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        def stream(self, request: dict[str, Any]):
            self.requests.append(request)
            if len(self.requests) == 1:
                return iter(_delegate("investigar logs"))
            return iter(_text_batch("Entendido, não vou reiniciar o subagente."))

    class _StoppedChildProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, _request: dict[str, Any]):
            self.calls += 1
            rows = [r for r in agents_holder[0].list() if r["name"] == "general"]
            assert len(rows) == 1
            agent_id = str(rows[0]["agent_id"])
            session_holder[0]._agent_controls().request_stop(
                TURN["user_id"], TURN["conversation_id"], agent_id
            )
            clock_holder[0].advance(1.5)
            return iter(_parallel_tool_calls_batch([("c1", "list_files", {"path": "."})]))

    main = _MainProvider()
    child = _StoppedChildProvider()
    engine, store, agents_store, session, clock = _make_env(tmp_path, main, child)
    engine_holder.append(engine)
    session_holder.append(session)
    clock_holder.append(clock)
    agents_holder.append(agents_store)
    try:
        runtime = session.build_runtime()
        try:
            result = runtime.run(TURN["turn_id"])
        finally:
            runtime.close()

        assert result.state == "completed"
        assert child.calls == 1
        assert len(main.requests) == 2

        second_req_text = "\n".join(str(m.get("content") or "") for m in main.requests[1]["messages"])
        assert "The user stopped subagent 'general' from the interface. Do not restart it unless the user asks." in second_req_text
        assert "STOPPED_BY_USER" in second_req_text

        failed_events = [
            (agent_id, payload)
            for ev, _summary, agent_id, payload in store.activity_events
            if ev == AgentActivityEventType.DELEGATION_FAILED
        ]
        assert len(failed_events) == 1
        assert failed_events[0][1]["error_code"] == "STOPPED_BY_USER"

        sub_row = next(r for r in agents_store.list() if r["name"] == "general")
        assert sub_row["state"] == "cancelled"

        with engine.connect() as conn:
            ctrl_rows = conn.execute(select(conversation_agent_controls)).mappings().all()
        assert len(ctrl_rows) == 1
        assert ctrl_rows[0]["outcome"] == "stopped"
    finally:
        engine.dispose()


def test_message_recorded_during_subagent_reaches_subagent_reminders_and_emits_user_activity(
    tmp_path: Path,
) -> None:
    """Case b): a user message reaches the subagent's ReminderQueue and emits AGENT_MESSAGE_SENT with source='user'."""
    session_holder: list[TurnSession] = []
    clock_holder: list[_FakeClock] = []
    agents_holder: list[ConversationAgentStore] = []

    class _MessagingChildProvider:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        def stream(self, request: dict[str, Any]):
            self.requests.append(request)
            if len(self.requests) == 1:
                rows = [r for r in agents_holder[0].list() if r["name"] == "general"]
                agent_id = str(rows[0]["agent_id"])
                session_holder[0]._agent_controls().post_message(
                    TURN["user_id"],
                    TURN["conversation_id"],
                    agent_id,
                    "Priorize o arquivo auth.py",
                )
                clock_holder[0].advance(1.5)
                return iter(_parallel_tool_calls_batch([("c1", "list_files", {"path": "."})]))
            return iter(_text_batch("Concluído focando em auth.py."))

    child = _MessagingChildProvider()
    engine, store, agents_store, session, clock = _make_env(tmp_path, child, child)
    session_holder.append(session)
    clock_holder.append(clock)
    agents_holder.append(agents_store)
    try:
        outcome = session._run_subagent("general", "Analise o módulo")
        assert outcome.status == "succeeded"
        assert len(child.requests) == 2

        second_messages_text = "\n".join(str(m.get("content") or "") for m in child.requests[1]["messages"])
        assert "Message from the user (sent to you directly while you work):\nPriorize o arquivo auth.py" in second_messages_text

        sub_row = next(r for r in agents_store.list() if r["name"] == "general")
        sub_id = str(sub_row["agent_id"])

        user_msg_events = [
            (summary, agent_id, payload)
            for ev, summary, agent_id, payload in store.activity_events
            if ev == AgentActivityEventType.AGENT_MESSAGE_SENT and payload.get("source") == "user"
        ]
        assert len(user_msg_events) == 1
        summary, event_agent_id, payload = user_msg_events[0]
        assert summary == "Você enviou uma mensagem para general"
        assert event_agent_id == sub_id
        assert payload["sender_name"] == "Você"
        assert payload["recipient_name"] == "general"
        assert payload["to_agent_id"] == sub_id
        assert payload["content"] == "Priorize o arquivo auth.py"
        assert payload["source"] == "user"

        with engine.connect() as conn:
            ctrl_rows = conn.execute(select(conversation_agent_controls)).mappings().all()
        assert len(ctrl_rows) == 1
        assert ctrl_rows[0]["outcome"] == "delivered"
    finally:
        engine.dispose()


def test_stale_pending_stop_does_not_kill_resumed_subagent(tmp_path: Path) -> None:
    """Case c): a stale pending stop is expired on start so resuming the subagent succeeds."""

    class _SimpleChild:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, _request: dict[str, Any]):
            self.calls += 1
            return iter(_text_batch(f"Resposta {self.calls}"))

    child = _SimpleChild()
    engine, _store, agents_store, session, clock = _make_env(tmp_path, child, child)
    try:
        first = session._run_subagent("general", "Primeira etapa")
        assert first.status == "succeeded"
        agent_id = str(first.payload["agent_id"])

        # Enqueue a stale stop while the agent is between runs
        stale_ctrl_id = session._agent_controls().request_stop(
            TURN["user_id"], TURN["conversation_id"], agent_id
        )
        clock.advance(2.0)

        resumed = session._run_subagent("general", "Segunda etapa", agent_id=agent_id)
        assert resumed.status == "succeeded"
        assert "Resposta 2" in resumed.content
        assert agents_store.get(agent_id)["state"] == "completed"

        with engine.connect() as conn:
            ctrl_rows = conn.execute(select(conversation_agent_controls)).mappings().all()
        assert len(ctrl_rows) == 1
        assert ctrl_rows[0]["control_id"] == stale_ctrl_id
        assert ctrl_rows[0]["outcome"] == "expired"
    finally:
        engine.dispose()


def test_leftover_message_at_end_of_subagent_is_forwarded_to_main_reminders(tmp_path: Path) -> None:
    """Case d): a message that remains unconsumed when the subagent finishes is forwarded to main _reminders."""
    session_holder: list[TurnSession] = []
    agents_holder: list[ConversationAgentStore] = []

    class _FinishingChild:
        def stream(self, _request: dict[str, Any]):
            rows = [r for r in agents_holder[0].list() if r["name"] == "general"]
            agent_id = str(rows[0]["agent_id"])
            # Posted within the 1s throttle window as the subagent is already finishing its final answer
            session_holder[0]._agent_controls().post_message(
                TURN["user_id"],
                TURN["conversation_id"],
                agent_id,
                "Inclua também o resumo executivo",
            )
            return iter(_text_batch("Relatório pronto."))

    child = _FinishingChild()
    engine, _store, agents_store, session, _clock = _make_env(tmp_path, child, child)
    session_holder.append(session)
    agents_holder.append(agents_store)
    # The Orin's turn is still running, so the leftover reaches it as a reminder (not a new scheduled turn).
    BACKGROUND_AGENTS.begin_turn(TURN["conversation_id"])
    try:
        outcome = session._run_subagent("general", "Gere o relatório")
        assert outcome.status == "succeeded"
        agent_id = str(outcome.payload["agent_id"])

        drained = session._reminders.drain()
        assert len(drained) == 1
        assert drained[0].kind == "user_message"
        assert (
            f"The user sent this to subagent general ({agent_id}), which finished before reading it:\n"
            "Inclua também o resumo executivo"
        ) in drained[0].text

        with engine.connect() as conn:
            ctrl_rows = conn.execute(select(conversation_agent_controls)).mappings().all()
        assert len(ctrl_rows) == 1
        assert ctrl_rows[0]["outcome"] == "forwarded"
    finally:
        BACKGROUND_AGENTS.end_turn(TURN["conversation_id"])
        engine.dispose()


def test_stopping_parent_subagent_stops_its_children(tmp_path: Path) -> None:
    """Case e): stopping a subagent stops the child subagents it started."""
    session_holder: list[TurnSession] = []
    clock_holder: list[_FakeClock] = []
    agents_holder: list[ConversationAgentStore] = []

    class _MainProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, _request: dict[str, Any]):
            self.calls += 1
            if self.calls == 1:
                return iter(_delegate("tarefa nivel 1"))
            return iter(_text_batch("Turno principal segue após parada da árvore."))

    class _TreeChildProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, _request: dict[str, Any]):
            self.calls += 1
            if self.calls == 1:
                # Level 1 delegates to Level 2
                return iter(_delegate("tarefa nivel 2"))
            # Now inside Level 2: user stops Level 1 (the parent subagent)
            rows = {str(r["name"]): r for r in agents_holder[0].list()}
            level1_id = str(rows["general"]["agent_id"])
            session_holder[0]._agent_controls().request_stop(
                TURN["user_id"], TURN["conversation_id"], level1_id
            )
            clock_holder[0].advance(1.5)
            return iter(_parallel_tool_calls_batch([("c-leaf", "list_files", {"path": "."})]))

    main = _MainProvider()
    child = _TreeChildProvider()
    engine, store, agents_store, session, clock = _make_env(tmp_path, main, child)
    session_holder.append(session)
    clock_holder.append(clock)
    agents_holder.append(agents_store)
    try:
        runtime = session.build_runtime()
        try:
            result = runtime.run(TURN["turn_id"])
        finally:
            runtime.close()

        assert result.state == "completed"
        assert child.calls == 2

        rows = {str(r["name"]): r for r in agents_store.list()}
        assert rows["general"]["state"] == "cancelled"
        assert rows["general-2"]["state"] == "cancelled"

        failed_by_agent = {
            agent_id: payload["error_code"]
            for ev, _summary, agent_id, payload in store.activity_events
            if ev == AgentActivityEventType.DELEGATION_FAILED
        }
        assert failed_by_agent[str(rows["general"]["agent_id"])] == "STOPPED_BY_USER"
        assert failed_by_agent[str(rows["general-2"]["agent_id"])] == "TURN_CANCELLED"
    finally:
        engine.dispose()


def test_watcher_one_second_throttle_and_error_resilience(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case f): _AgentControlWatcher polls at most once per second via time.monotonic and never raises on DB errors."""
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'throttle.db').as_posix()}")
    metadata.create_all(engine)
    try:
        real_store = AgentControlStore(engine)
        take_calls = 0
        orig_take = real_store.take

        def counting_take(*args, **kwargs):
            nonlocal take_calls
            take_calls += 1
            return orig_take(*args, **kwargs)

        monkeypatch.setattr(real_store, "take", counting_take)

        fake_now = [50.0]
        monkeypatch.setattr(time, "monotonic", lambda: fake_now[0])

        event = threading.Event()
        reminders = ReminderQueue()
        watcher = _AgentControlWatcher(
            store=real_store,
            user_id="u1",
            conversation_id="c1",
            agent_id="agent:c1:1",
            clean="Explore",
            event=event,
            reminders=reminders,
        )
        # expire_pending called take once during __init__
        assert take_calls == 1

        # First poll at t=50.0 runs immediately
        watcher.poll()
        assert take_calls == 2

        # Enqueue a message and stop at t=50.3 (< 1.0s since last poll)
        fake_now[0] = 50.3
        real_store.post_message("u1", "c1", "agent:c1:1", "mensagem rápida")
        real_store.request_stop("u1", "c1", "agent:c1:1")

        watcher.poll()
        fake_now[0] = 50.99
        watcher.poll()
        # Still throttled!
        assert take_calls == 2
        assert not event.is_set()
        assert len(reminders) == 0

        # At t=51.0 (1.0s elapsed), poll executes and consumes both controls
        fake_now[0] = 51.0
        watcher.poll()
        assert take_calls == 3
        assert event.is_set()
        assert watcher.stopped_by_user is True
        drained = reminders.drain()
        assert len(drained) == 1
        assert "mensagem rápida" in drained[0].text

        # Database errors are swallowed and logged at debug
        class _BrokenStore:
            def expire_pending(self, *_a, **_kw):
                raise RuntimeError("db down")

            def take(self, *_a, **_kw):
                raise RuntimeError("db down")

            def set_outcome(self, *_a, **_kw):
                raise RuntimeError("db down")

        broken_watcher = _AgentControlWatcher(
            store=_BrokenStore(),
            user_id="u1",
            conversation_id="c1",
            agent_id="agent:c1:broken",
            clean="Explore",
            event=threading.Event(),
            clock=lambda: 200.0,
        )
        broken_watcher.poll()
        broken_watcher.finalize()
    finally:
        engine.dispose()


def test_subagent_control_routes_202_404_409_and_422(tmp_path: Path) -> None:
    """Route tests for POST /stop and POST /messages: 202, 404 wrong conv/user, 409 AGENT_NOT_RUNNING, 422."""
    from fastapi.testclient import TestClient

    from agentos.agentic.settings import AgentRuntimeSettingsStore
    from agentos.api.gateway import ApiServices, create_app
    from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
    from agentos.conversations.chat import ChatApplication

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'routes.db').as_posix()}")
    metadata.create_all(engine)
    try:
        store = PostgresChatStore(engine)
        chat_app = ChatApplication(store)
        security = InMemorySecurityService()
        security.add_pat(
            "pat-u1",
            AuthenticatedPrincipal(user_id="user-1", credential_ref="pat-1", scopes=frozenset({"api"})),
        )
        security.add_pat(
            "pat-u2",
            AuthenticatedPrincipal(user_id="user-2", credential_ref="pat-2", scopes=frozenset({"api"})),
        )
        services = ApiServices(
            security=security,
            conversation_application=chat_app,
            agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"),
            workspace_root=tmp_path / "workspaces",
        )
        client = TestClient(create_app(services))
        h1 = {"Authorization": "Bearer pat-u1"}
        h2 = {"Authorization": "Bearer pat-u2"}

        conv1 = chat_app.create(
            user_id="user-1",
            message="Conversa 1",
            provider="openrouter",
            model_id="m1",
            idempotency_key="idem-r1",
        ).conversation_id
        conv2 = chat_app.create(
            user_id="user-1",
            message="Conversa 2",
            provider="openrouter",
            model_id="m1",
            idempotency_key="idem-r2",
        ).conversation_id

        agents1 = ConversationAgentStore(engine, conversation_id=conv1, user_id="user-1")
        active_agent = agents1.create("explorer", "Explore", provider="openrouter", model_id="m1", parent_agent_id="agent:main")
        agents1.set_state(str(active_agent["agent_id"]), "working")

        done_agent = agents1.create("reviewer", "Explore", provider="openrouter", model_id="m1", parent_agent_id="agent:main")
        agents1.set_state(str(done_agent["agent_id"]), "completed")

        agents2 = ConversationAgentStore(engine, conversation_id=conv2, user_id="user-1")
        other_conv_agent = agents2.create("other", "Explore", provider="openrouter", model_id="m1", parent_agent_id="agent:main")
        agents2.set_state(str(other_conv_agent["agent_id"]), "running")

        # 202 happy path
        res_stop = client.post(f"/v1/conversations/{conv1}/agents/{active_agent['agent_id']}/stop", headers=h1)
        assert res_stop.status_code == 202
        assert res_stop.json()["status"] == "stopping"
        assert res_stop.json()["agent_id"] == active_agent["agent_id"]

        res_msg = client.post(
            f"/v1/conversations/{conv1}/agents/{active_agent['agent_id']}/messages",
            json={"message": "Use o banco de testes"},
            headers=h1,
        )
        assert res_msg.status_code == 202
        assert res_msg.json()["status"] == "queued"
        assert res_msg.json()["agent_id"] == active_agent["agent_id"]

        # 404 agent from another conversation
        assert (
            client.post(f"/v1/conversations/{conv1}/agents/{other_conv_agent['agent_id']}/stop", headers=h1).status_code
            == 404
        )
        assert (
            client.post(
                f"/v1/conversations/{conv1}/agents/{other_conv_agent['agent_id']}/messages",
                json={"message": "oi"},
                headers=h1,
            ).status_code
            == 404
        )

        # 404 conversation of another user
        assert (
            client.post(f"/v1/conversations/{conv1}/agents/{active_agent['agent_id']}/stop", headers=h2).status_code
            == 404
        )
        assert (
            client.post(
                f"/v1/conversations/{conv1}/agents/{active_agent['agent_id']}/messages",
                json={"message": "oi"},
                headers=h2,
            ).status_code
            == 404
        )

        # 409 AGENT_NOT_RUNNING when agent is completed
        conflict_stop = client.post(f"/v1/conversations/{conv1}/agents/{done_agent['agent_id']}/stop", headers=h1)
        assert conflict_stop.status_code == 409
        assert conflict_stop.json()["error"]["code"] == "AGENT_NOT_RUNNING"

        conflict_msg = client.post(
            f"/v1/conversations/{conv1}/agents/{done_agent['agent_id']}/messages",
            json={"message": "oi"},
            headers=h1,
        )
        assert conflict_msg.status_code == 409
        assert conflict_msg.json()["error"]["code"] == "AGENT_NOT_RUNNING"

        # 422 empty or >8000 message
        assert (
            client.post(
                f"/v1/conversations/{conv1}/agents/{active_agent['agent_id']}/messages",
                json={"message": ""},
                headers=h1,
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/v1/conversations/{conv1}/agents/{active_agent['agent_id']}/messages",
                json={"message": "x" * 8001},
                headers=h1,
            ).status_code
            == 422
        )
    finally:
        engine.dispose()


def _memory_watcher(**kwargs: Any):
    from agentos.conversations.agent_controls import InMemoryAgentControls

    store = InMemoryAgentControls()
    main = ReminderQueue()
    params: dict[str, Any] = {
        "store": store,
        "user_id": "u1",
        "conversation_id": "c1",
        "agent_id": "agent:c1:1",
        "clean": "Explore",
        "event": threading.Event(),
        "main_reminders": main,
        "clock": _FakeClock(),
    }
    params.update(kwargs)
    return store, main, _AgentControlWatcher(**params)


def _outcome(store: Any, control_id: str) -> object:
    return next(row["outcome"] for row in store._rows if row["control_id"] == control_id)


def test_message_queued_but_never_read_is_forwarded_to_the_orin_at_the_end() -> None:
    """Review #1: a message polled into the subagent's queue during its last model call is not lost."""
    store, main, watcher = _memory_watcher()
    control_id = store.post_message("u1", "c1", "agent:c1:1", "Inclua o resumo")
    watcher.poll()
    assert _outcome(store, control_id) == "delivered"

    watcher.finalize()  # the subagent ended without another model request: its queue was never drained

    assert len(watcher.reminders) == 0
    forwarded = main.drain()
    assert len(forwarded) == 1
    assert "which finished before reading it:\nInclua o resumo" in forwarded[0].text
    assert _outcome(store, control_id) == "forwarded"


def test_message_read_by_the_subagent_is_not_forwarded_again() -> None:
    store, main, watcher = _memory_watcher()
    control_id = store.post_message("u1", "c1", "agent:c1:1", "Priorize auth.py")
    watcher.poll()
    assert len(watcher.reminders.drain()) == 1  # the runtime drained it before the next model request

    watcher.finalize()

    assert len(main) == 0
    assert _outcome(store, control_id) == "delivered"


def test_message_after_a_stop_or_with_a_full_queue_goes_to_the_orin() -> None:
    store, main, watcher = _memory_watcher()
    store.request_stop("u1", "c1", "agent:c1:1")
    late = store.post_message("u1", "c1", "agent:c1:1", "Na verdade, continue")
    watcher.poll()

    assert watcher.stopped_by_user is True
    assert len(watcher.reminders) == 0
    assert "Na verdade, continue" in main.drain()[0].text
    assert _outcome(store, late) == "forwarded"

    full = ReminderQueue(max_items=1)
    full.add("note", "already waiting")
    store2, main2, watcher2 = _memory_watcher(reminders=full)
    overflow = store2.post_message("u1", "c1", "agent:c1:1", "Não cabe na fila")
    watcher2.poll()
    assert "Não cabe na fila" in main2.drain()[0].text
    assert _outcome(store2, overflow) == "forwarded"


def test_leftover_message_after_the_orins_turn_ended_starts_a_new_turn() -> None:
    """Review #2: with the leader's turn over, its reminder queue is dead; the message must open a new turn."""
    woken: list[tuple[str, str]] = []
    store, main, watcher = _memory_watcher(
        main_turn_active=lambda: False,
        wake_main=lambda text, control_id: woken.append((text, control_id)),
    )
    control_id = store.post_message("u1", "c1", "agent:c1:1", "Revise também o README")

    watcher.finalize()

    assert len(main) == 0
    assert len(woken) == 1
    assert "Revise também o README" in woken[0][0]
    assert woken[0][1] == control_id
    assert _outcome(store, control_id) == "forwarded"


def test_user_message_waker_schedules_a_turn_once_per_control(tmp_path: Path) -> None:
    class _Unused:
        def stream(self, _request: dict[str, Any]):
            raise AssertionError("no model call expected")

    engine, _store, _agents, session, _clock = _make_env(tmp_path, _Unused(), _Unused())
    try:
        created: list[tuple[str, Any, str]] = []

        class _Scheduler:
            def create(self, user_id: str, request: Any, *, idempotency_key: str) -> None:
                created.append((user_id, request, idempotency_key))

        session._scheduler_service = _Scheduler()
        wake = session._user_message_waker(TURN["conversation_id"])
        assert wake is not None
        wake("The user sent this to subagent x", "ctrl_abc")

        assert len(created) == 1
        user_id, request, key = created[0]
        assert user_id == TURN["user_id"]
        assert key == "agent-msg:ctrl_abc"
        assert request.conversation_id == TURN["conversation_id"]
    finally:
        engine.dispose()


def test_stopped_by_user_never_counts_as_a_repeated_tool_failure() -> None:
    """Review #3: asking again for a subagent the person stopped is not a retry loop."""
    from agentos.agentic.runtime import POLICY_REFUSAL_CODES

    assert "STOPPED_BY_USER" in POLICY_REFUSAL_CODES


def test_control_accepted_after_the_agent_finished_is_withdrawn_with_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review #4: the agent may finish between the state check and the insert; nothing would ever read it."""
    from agentos.api.contracts import ApplicationConflictError

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'race.db').as_posix()}")
    metadata.create_all(engine)
    try:
        store = PostgresChatStore(engine)
        states = iter([True, False, True, False])
        monkeypatch.setattr(store, "_agent_is_running", lambda *_a: next(states))

        with pytest.raises(ApplicationConflictError):
            store.send_agent_message("c1", "u1", "agent:c1:1", "tarde demais")
        with pytest.raises(ApplicationConflictError):
            store.request_agent_stop("c1", "u1", "agent:c1:1")

        with engine.connect() as conn:
            rows = conn.execute(select(conversation_agent_controls)).mappings().all()
        assert len(rows) == 2
        assert all(row["outcome"] == "expired" and row["consumed_at"] is not None for row in rows)

        monkeypatch.setattr(store, "_agent_is_running", lambda *_a: True)
        accepted = store.send_agent_message("c1", "u1", "agent:c1:1", "a tempo")
        assert accepted["status"] == "queued"
    finally:
        engine.dispose()
