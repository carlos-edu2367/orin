"""Red Team regression suite for Entrega 2 (Bloco Mensagens — 9 findings)."""
from __future__ import annotations

from concurrent.futures import Future
from datetime import timedelta
import json
from pathlib import Path
import threading
import time
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.agentic.native_turn import NativeTurnRunner, RuntimeToolExecutor
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.reminders import ReminderQueue
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.session import TurnSession, _AgentControlWatcher
from agentos.agentic.tools.orin.friend_delivery_watch import (
    FriendDeliveryNotification,
    FriendDeliveryWatcherRegistry,
    get_friend_delivery_watcher,
)
from agentos.agentic.tools.orin.session_delivery import (
    FRIEND_MESSAGE_PREFIX,
    deliver_to_session,
)
from agentos.agentic.workspace import ConversationWorkspace
from agentos.api.contracts import ApplicationValidationError
from agentos.conversations.agent_controls import AgentControlStore, InMemoryAgentControls
from agentos.conversations.chat import BLOCKED_PROMPT_PLACEHOLDER, PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import (
    conversation_activity_events,
    conversation_input_queue,
    conversations,
    metadata,
)
from agentos.providers.cli_tools_mcp.types import ToolCallRequest
from agentos.settings.loader import empty_settings
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN,
    _RecordingStore,
    _text_batch,
)


def _make_engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    return engine


def _seed_friend_and_code(
    store: PostgresChatStore,
    *,
    live: bool = True,
) -> tuple[str, str, str, dict[str, Any]]:
    friend_rec = store.create(
        user_id="u1",
        message="Oi Friend",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="rt-friend-seed",
        code_mode="friend",
    )
    code_rec = store.create(
        user_id="u1",
        message="Implemente X",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="rt-code-seed",
        code_mode="code",
        permission_mode="accept_edits",
    )
    claimed = store.claim(code_rec.turn_id)
    assert claimed is not None
    if not live:
        store.finish(claimed)
    return friend_rec.conversation_id, code_rec.conversation_id, code_rec.turn_id, claimed


# ---------------------------------------------------------------------------
# Finding #1 [CRITICAL]: SendMessage in RuntimeToolExecutor / FOLLOW_UP_TOOLS
# ---------------------------------------------------------------------------


def test_redteam_01_send_message_available_immediately_after_bg_subagent_in_executor(
    tmp_path: Path,
) -> None:
    """After starting a background subagent, RuntimeToolExecutor must report tools_changed=True,
    expose SendMessage in list_tools(), and accept call_tool('SendMessage', ...)."""
    release_child = threading.Event()
    child_started = threading.Event()

    class _BlockingChildProvider:
        def stream(self, request: dict[str, Any]):
            child_started.set()
            release_child.wait(timeout=5.0)
            return iter(_text_batch("done"))

    store = _RecordingStore()
    turn = {
        **TURN,
        "conversation_id": "conv-rt-01",
        "turn_id": "turn-rt-01",
        "code_mode": "code",
        "conversation_mode": "code",
    }
    session = TurnSession(
        store=store,
        turn=turn,
        provider_factory=lambda: _BlockingChildProvider(),
        child_provider_factory=lambda _m, **_r: _BlockingChildProvider(),
        workspace_root=tmp_path / "ws",
        enable_subagents=True,
    )
    session.effective_settings = empty_settings()
    runtime = session.build_runtime()
    executor = RuntimeToolExecutor(runtime, turn, allow_deferral=True, gather_seconds=0.005)
    try:
        res_start = executor.call_tool(
            ToolCallRequest(
                name="Agent",
                arguments={
                    "description": "background task",
                    "prompt": "wait for message",
                    "subagent_type": "Explore",
                    "run_in_background": True,
                },
            )
        )
        assert not res_start.is_error, res_start.text
        assert res_start.tools_changed is True, "Launching background subagent must trigger tools_changed=True"

        tool_names = {spec.name for spec in executor.list_tools()}
        assert "SendMessage" in tool_names, f"SendMessage missing from list_tools(): {sorted(tool_names)}"
        assert "CheckSubagent" in tool_names

        # Extract child agent_id from registered background subagents
        bg_ids = list(session._background_subagents.keys())
        assert len(bg_ids) == 1
        child_id = bg_ids[0]
        assert child_started.wait(timeout=3.0)

        res_msg = executor.call_tool(
            ToolCallRequest(
                name="SendMessage",
                arguments={"agent_id": child_id, "message": "Use a abordagem B"},
            )
        )
        assert not res_msg.is_error, res_msg.text
        assert "Unknown tool" not in res_msg.text
    finally:
        release_child.set()
        for entry in list(session._background_subagents.values()):
            fut = entry.get("future")
            if fut is not None:
                try:
                    fut.result(timeout=3.0)
                except Exception:
                    pass
        executor.close()


def test_redteam_01_cli_without_tool_list_changed_advertises_send_message_initially(
    tmp_path: Path,
) -> None:
    """CLIs with tool_list_changed=False (allow_deferral=False) only read tools/list on boot;
    SendMessage must be advertised initially when parent messaging or teams are enabled."""
    store = _RecordingStore()
    turn = {
        **TURN,
        "conversation_id": "conv-rt-01-cli",
        "turn_id": "turn-rt-01-cli",
        "code_mode": "code",
        "conversation_mode": "code",
    }
    session = TurnSession(
        store=store,
        turn=turn,
        provider_factory=lambda: type("P", (), {"stream": lambda self, req: iter(_text_batch("ok"))})(),
        workspace_root=tmp_path / "ws",
        enable_subagents=True,
    )
    session.effective_settings = empty_settings()
    runtime = session.build_runtime()
    executor = RuntimeToolExecutor(runtime, turn, allow_deferral=False)
    try:
        initial_names = {spec.name for spec in executor.list_tools()}
        assert "SendMessage" in initial_names, sorted(initial_names)
    finally:
        executor.close()


# ---------------------------------------------------------------------------
# Finding #2 [CRITICAL]: friend.delivery_status in conversation_activity_events
# and durable cross-turn delivery confirmation for Friend
# ---------------------------------------------------------------------------


def test_redteam_02_friend_delivery_status_recorded_in_activity_events_and_replayed_via_sse(
    tmp_path: Path,
) -> None:
    """friend.delivery_status must be persisted in conversation_activity_events so SSE
    GET /v1/conversations/{friend_cid}/events and reload snapshots receive it live."""
    assert AgentActivityEventType.FRIEND_DELIVERY_STATUS.value == "friend.delivery_status"

    engine = _make_engine()
    activity_store = PostgresAgenticActivityStore(engine, "test-secret-key")
    store = PostgresChatStore(engine, activity_store=activity_store)
    friend_cid, code_cid, _live_tid, claimed_turn = _seed_friend_and_code(store, live=True)

    reminders = ReminderQueue()
    toolset = AgentToolset(
        ConversationWorkspace.at_root(tmp_path),
        chat_store=store,
        project_user_id="u1",
        conversation_id=friend_cid,
        default_provider="anthropic",
        default_model_id="claude-sonnet-4-5",
        reminders=reminders,
    )
    toolset._effective_settings = empty_settings()

    outcome = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": code_cid,
            "message": "Valide o contrato SSE.",
        },
        call_id="rt02-call-1",
    )
    assert outcome.status == "succeeded"
    queue_id = str(outcome.payload["queue_id"])

    claimed_items = store.claim_queued_inputs(claimed_turn, notify_friend=True)
    assert len(claimed_items) == 1

    # 1) Directly present in conversation_activity_events for the Friend conversation
    with engine.connect() as conn:
        act_rows = conn.execute(
            select(
                conversation_activity_events.c.event_type,
                conversation_activity_events.c.payload,
            ).where(
                conversation_activity_events.c.conversation_id == friend_cid,
                conversation_activity_events.c.event_type == "friend.delivery_status",
            )
        ).mappings().all()
    assert len(act_rows) == 1, act_rows
    assert act_rows[0]["payload"]["status"] == "injected"
    assert act_rows[0]["payload"]["queue_id"] == queue_id
    assert act_rows[0]["payload"]["target_conversation_id"] == code_cid

    # 2) Returned by store.events(friend_cid, "u1", "0") (the SSE replay query!)
    replayed, _cursor = store.events(friend_cid, "u1", "0")
    matching = [ev for ev in replayed if ev.get("event_type") == "friend.delivery_status"]
    assert len(matching) == 1, replayed
    assert matching[0]["payload"]["status"] == "injected"
    assert matching[0]["payload"]["queue_id"] == queue_id


def test_redteam_02_friend_delivery_confirmation_survives_after_friend_turn_ended(
    tmp_path: Path,
) -> None:
    """When the Friend turn that called manage_session has already ended (its ReminderQueue
    was unregistered), the confirmation must still be delivered to the next Friend turn."""
    engine = _make_engine()
    activity_store = PostgresAgenticActivityStore(engine, "test-secret-key")
    store = PostgresChatStore(engine, activity_store=activity_store)
    friend_cid, code_cid, live_tid, _claimed = _seed_friend_and_code(store, live=True)

    registry = FriendDeliveryWatcherRegistry()
    old_queue = ReminderQueue()
    registry.register_target(friend_conversation_id=friend_cid, reminder_queue=old_queue)
    # Simulate Friend turn 1 ending
    registry.unregister_target(friend_conversation_id=friend_cid, reminder_queue=old_queue)

    # Target session injects the message AFTER Friend turn 1 ended
    notif = registry.notify(
        status="injected",
        user_id="u1",
        target_conversation_id=code_cid,
        queue_id="q-after-turn-1",
        origin_conversation_id=friend_cid,
        target_turn_id=live_tid,
        store=store,
    )
    assert notif is not None
    assert old_queue.drain() == []

    # Friend turn 2 starts with a brand new ReminderQueue and drains pending confirmations
    new_queue = ReminderQueue()
    count = registry.drain_pending_for_friend(
        friend_cid,
        new_queue,
        store=store,
        user_id="u1",
    )
    assert count >= 1
    drained = new_queue.drain()
    assert any("entregue no turno em andamento" in r.text.lower() for r in drained), drained

    # Also test durable recovery after a fresh registry (simulating process restart!)
    fresh_registry = FriendDeliveryWatcherRegistry()
    restart_queue = ReminderQueue()
    durable_count = fresh_registry.drain_pending_for_friend(
        friend_cid,
        restart_queue,
        store=store,
        user_id="u1",
    )
    assert durable_count >= 1
    assert any("entregue no turno em andamento" in r.text.lower() for r in restart_queue.drain())


# ---------------------------------------------------------------------------
# Finding #3 [CRITICAL]: Silent message loss in watcher.finalize() window
# ---------------------------------------------------------------------------


def test_redteam_03_teammate_message_not_lost_during_watcher_finalize_window(
    tmp_path: Path,
) -> None:
    """When a teammate's watcher has closed/finalized while its Future is not done yet
    and mate['busy'] is still True, _send_team_message must route to mailbox (never strand
    a row in conversation_agent_controls)."""
    store = _RecordingStore()
    turn = {**TURN, "conversation_id": "conv-rt-03", "turn_id": "turn-rt-03"}
    session = TurnSession(
        store=store,
        turn=turn,
        provider_factory=lambda: type("P", (), {"stream": lambda self, req: iter(_text_batch("ok"))})(),
        workspace_root=tmp_path / "ws",
        enable_subagents=True,
    )
    session.effective_settings = empty_settings()

    mate_agent_id = "agent:conv-rt-03:frank001"
    pending_future: Future[Any] = Future()  # Still not done (saving report / checking mailbox)
    with session._subagent_lock:
        session._background_subagents[mate_agent_id] = {
            "future": pending_future,
            "agent_id": mate_agent_id,
            "agent_name": "frank",
        }
        session._watcher_closed.add(mate_agent_id)
    with session._team_lock:
        session._team_roster()["frank"] = {
            "agent_id": mate_agent_id,
            "team_name": "alpha",
            "type": "general-purpose",
            "busy": True,
        }

    # _is_subagent_running must be False once watcher closed!
    assert session._is_subagent_running(mate_agent_id) is False

    outcome = session._send_team_message(
        sender="lead",
        to="frank",
        message="NEVER LOSE THIS",
        summary="critical",
    )
    assert outcome.status == "succeeded"

    # Must NOT have stranded an unread control in conversation_agent_controls
    controls = session._agent_controls().list_for_agent("user-1", "conv-rt-03", mate_agent_id)
    assert controls == [], f"Stranded controls: {controls}"

    # Must be in the teammate's mailbox so _take_team_mail picks it up!
    assert session._team_pending("frank") == 1
    task, taken = session._take_team_mail("frank")
    assert "NEVER LOSE THIS" in task
    assert len(taken) == 1


def test_redteam_03_subagent_message_returns_not_running_after_watcher_closed(
    tmp_path: Path,
) -> None:
    """For a normal subagent whose watcher already finalized while its Future is still
    completing teardown, SendMessage(agent_id=...) must return AGENT_NOT_RUNNING without stranding."""
    store = _RecordingStore()
    turn = {**TURN, "conversation_id": "conv-rt-03b", "turn_id": "turn-rt-03b"}
    session = TurnSession(
        store=store,
        turn=turn,
        provider_factory=lambda: type("P", (), {"stream": lambda self, req: iter(_text_batch("ok"))})(),
        workspace_root=tmp_path / "ws",
        enable_subagents=True,
    )
    session.effective_settings = empty_settings()

    child_id = "agent:conv-rt-03b:worker1"
    pending_future: Future[Any] = Future()
    with session._subagent_lock:
        session._subagent_records[child_id] = {"name": "worker", "parent_agent_id": session.main_agent_id}
        session._background_subagents[child_id] = {"future": pending_future, "agent_id": child_id}
        session._watcher_closed.add(child_id)

    res = session._send_agent_message(agent_id=child_id, message="late note")
    assert res.status == "failed"
    assert res.error_code == "AGENT_NOT_RUNNING"
    assert session._agent_controls().list_for_agent("user-1", "conv-rt-03b", child_id) == []


# ---------------------------------------------------------------------------
# Finding #4 [HIGH]: _claim_queued_user_inputs enforcing USER_PROMPT_SUBMIT
# ---------------------------------------------------------------------------


def test_redteam_04_foreground_subagent_claim_enforces_user_prompt_submit_hook(
    tmp_path: Path,
) -> None:
    """TurnSession._claim_queued_user_inputs must emit HookEvent.USER_PROMPT_SUBMIT,
    block refused items via mark_queued_input_blocked, and only confirm approved Friend items."""
    engine = _make_engine()
    activity_store = PostgresAgenticActivityStore(engine, "test-secret-key")
    store = PostgresChatStore(engine, activity_store=activity_store)
    friend_cid, code_cid, _live_tid, claimed_turn = _seed_friend_and_code(store, live=True)

    # Enqueue two Friend messages: first blocked by hook, second allowed with additional_context
    q_blocked = store.enqueue_input(
        user_id="u1",
        conversation_id=code_cid,
        content="FORBIDDEN_SECRET",
        origin="friend",
        origin_conversation_id=friend_cid,
    )
    q_allowed = store.enqueue_input(
        user_id="u1",
        conversation_id=code_cid,
        content="Rodar testes",
        origin="friend",
        origin_conversation_id=friend_cid,
    )

    bus = LifecycleBus()
    bus.subscribe(
        lambda ev: (
            LifecycleDecision(blocked=True, reason="segredo bloqueado")
            if ev.name is HookEvent.USER_PROMPT_SUBMIT and "FORBIDDEN_SECRET" in str(ev.payload.get("prompt") or "")
            else (
                LifecycleDecision(additional_context=["contexto-extra-hook"])
                if ev.name is HookEvent.USER_PROMPT_SUBMIT
                else None
            )
        )
    )

    session = TurnSession(
        store=store,
        turn=claimed_turn,
        provider_factory=lambda: type("P", (), {"stream": lambda self, req: iter(_text_batch("ok"))})(),
        workspace_root=tmp_path / "ws",
        lifecycle=bus,
        chat_store=store,
    )
    session.effective_settings = empty_settings()

    notifications: list[FriendDeliveryNotification] = []
    unsub = get_friend_delivery_watcher().subscribe(notifications.append)
    try:
        texts = session._claim_queued_user_inputs()
    finally:
        unsub()

    # Only the allowed message is returned, with Friend prefix + hook additional_context
    assert len(texts) == 1, texts
    assert texts[0].startswith(FRIEND_MESSAGE_PREFIX)
    assert "Rodar testes" in texts[0]
    assert "contexto-extra-hook" in texts[0]
    assert "FORBIDDEN_SECRET" not in texts[0]

    # DB state: blocked row erased to placeholder & state='blocked'
    with engine.connect() as conn:
        rows = {
            row["queue_id"]: dict(row)
            for row in conn.execute(
                select(
                    conversation_input_queue.c.queue_id,
                    conversation_input_queue.c.state,
                    conversation_input_queue.c.content,
                ).where(conversation_input_queue.c.conversation_id == code_cid)
            ).mappings().all()
        }
    assert rows[q_blocked["queue_id"]]["state"] == "blocked"
    assert rows[q_blocked["queue_id"]]["content"] == BLOCKED_PROMPT_PLACEHOLDER
    assert rows[q_allowed["queue_id"]]["state"] == "injected"

    # Watcher received blocked for q_blocked (never injected) and injected for q_allowed
    blocked_notifs = [n for n in notifications if n.queue_id == q_blocked["queue_id"]]
    allowed_notifs = [n for n in notifications if n.queue_id == q_allowed["queue_id"]]
    assert [n.status for n in blocked_notifs] == ["blocked"]
    assert [n.status for n in allowed_notifs] == ["injected"]


# ---------------------------------------------------------------------------
# Finding #5 [HIGH]: TOCTOU race post-enqueue_input in deliver_to_session
# ---------------------------------------------------------------------------


def test_redteam_05_deliver_to_session_toctou_promotes_if_turn_finishes_during_enqueue() -> None:
    """If the target live turn finishes during enqueue_input, deliver_to_session must recheck
    liveness and immediately promote the pending item into a new turn (no orphaned pending row)."""
    engine = _make_engine()
    store = PostgresChatStore(engine)
    friend_cid, code_cid, live_tid, claimed_turn = _seed_friend_and_code(store, live=True)

    original_enqueue = store.enqueue_input
    enqueue_calls: list[int] = []

    def _racy_enqueue(*args: Any, **kwargs: Any):
        enqueue_calls.append(1)
        item = original_enqueue(*args, **kwargs)
        # Simulate the running turn finishing right as enqueue commits!
        store.finish(claimed_turn)
        return item

    store.enqueue_input = _racy_enqueue  # type: ignore[method-assign]

    result = deliver_to_session(
        chat_store=store,
        user_id="u1",
        conversation_id=code_cid,
        text="Não fique órfã na fila.",
        delivery="now",
        origin="friend",
        origin_conversation_id=friend_cid,
        tool_call_id="rt05-toctou-1",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        permission_mode="accept_edits",
        code_mode="code",
        settings=empty_settings(),
    )

    assert enqueue_calls == [1], "Expected deliver_to_session to take the live-turn enqueue path"

    assert result.delivery in {"new_turn", "queued_next_turn"}, result
    assert result.turn_id is not None and result.turn_id != live_tid

    # Zero pending rows left orphaned in conversation_input_queue!
    with engine.connect() as conn:
        pending_rows = conn.execute(
            select(conversation_input_queue.c.queue_id, conversation_input_queue.c.state).where(
                conversation_input_queue.c.conversation_id == code_cid,
                conversation_input_queue.c.state == "pending",
            )
        ).mappings().all()
    assert pending_rows == [], pending_rows


# ---------------------------------------------------------------------------
# Finding #6 [HIGH]: _apply_polled_item vs ack_control + set_outcome regression
# ---------------------------------------------------------------------------


def test_redteam_06_set_outcome_injected_never_regresses_delivered() -> None:
    """AgentControlStore.set_outcome and InMemoryAgentControls.set_outcome must never
    overwrite 'delivered' (or other terminal outcomes) back to 'injected'."""
    engine = _make_engine()
    sql_store = AgentControlStore(engine)
    mem_store = InMemoryAgentControls()

    for store in (sql_store, mem_store):
        cid = store.post_message("u1", "c1", "agent-1", "oi")
        assert store.mark_delivered(cid) is True
        store.set_outcome(cid, "injected")
        rec = store.get(cid)
        assert rec is not None
        assert rec["state"] == "delivered", f"{type(store).__name__} regressed state to {rec['state']}"


def test_redteam_06_watcher_apply_polled_item_does_not_overwrite_concurrent_ack() -> None:
    """If the subagent drains reminders and calls ack_control() synchronously during
    reminders.add(), _apply_polled_item must NOT overwrite outcome='delivered' back to 'injected'."""
    engine = _make_engine()
    store = AgentControlStore(engine)
    cid = store.post_message("u1", "c1", "agent-1", "instrução urgente")
    taken = store.take("u1", "c1", "agent-1")
    assert len(taken) == 1

    watcher_ref: list[_AgentControlWatcher] = []

    class _ImmediateAckReminderQueue(ReminderQueue):
        def add(self, kind: str, text: str, *, key: str | None = None) -> bool:
            ok = super().add(kind, text, key=key)
            if ok and key and key.startswith("user-msg:") and watcher_ref:
                # Simulate subagent thread draining & acking BEFORE _apply_polled_item continues!
                watcher_ref[0].ack_control(key.split("user-msg:", 1)[1])
            return ok

    watcher = _AgentControlWatcher(
        store=store,
        user_id="u1",
        conversation_id="c1",
        agent_id="agent-1",
        clean="worker",
        event=threading.Event(),
    )
    watcher.reminders = _ImmediateAckReminderQueue()
    watcher_ref.append(watcher)

    watcher._apply_polled_item(taken[0])

    rec = store.get(cid)
    assert rec is not None
    assert rec["state"] == "delivered", rec
    assert rec["delivered_at"] is not None


# ---------------------------------------------------------------------------
# Finding #7 [MEDIUM]: Idempotency key collision & _active_invocation_id leak
# ---------------------------------------------------------------------------


def test_redteam_07_active_invocation_id_restored_in_finally(tmp_path: Path) -> None:
    """_active_invocation_id must be restored after _invoke_unobserved finishes and
    never leak call_id from one invocation to the next."""
    ws = ConversationWorkspace(tmp_path, "conv-rt-07")
    ws.write_text("note.txt", "hello")
    toolset = AgentToolset(ws)

    assert getattr(toolset, "_active_invocation_id", None) is None
    res1 = toolset.invoke("read_file", {"path": "note.txt"}, call_id="tool-call:0")
    assert res1.status == "succeeded"
    assert getattr(toolset, "_active_invocation_id", None) is None

    # Second call without call_id must NOT inherit "tool-call:0"
    observed: list[Any] = []
    orig_read = toolset.read_file

    def _spy_read(*args: Any, **kwargs: Any):
        observed.append(getattr(toolset, "_active_invocation_id", None))
        return orig_read(*args, **kwargs)

    toolset.read_file = _spy_read  # type: ignore[method-assign]
    toolset.refresh_definitions()
    res2 = toolset.invoke("read_file", {"path": "note.txt"})
    assert res2.status == "succeeded"
    assert observed == [None], observed


def test_redteam_07_sequential_tool_call_id_across_turns_does_not_collide(
    tmp_path: Path,
) -> None:
    """Sequential provider IDs like 'tool-call:0' across different Friend turns must not
    collide, while retrying the same call in the same turn remains idempotent; reusing a key
    for a different message on an earlier turn must raise ApplicationValidationError."""
    engine = _make_engine()
    store = PostgresChatStore(engine)
    friend_cid, code_cid, _live_tid, _claimed = _seed_friend_and_code(store, live=False)

    ts_turn_1 = AgentToolset(
        ConversationWorkspace.at_root(tmp_path),
        chat_store=store,
        project_user_id="u1",
        conversation_id=friend_cid,
        turn_id="friend-turn-1",
        default_provider="anthropic",
        default_model_id="claude-sonnet-4-5",
    )
    ts_turn_1._effective_settings = empty_settings()

    ts_turn_2 = AgentToolset(
        ConversationWorkspace.at_root(tmp_path),
        chat_store=store,
        project_user_id="u1",
        conversation_id=friend_cid,
        turn_id="friend-turn-2",
        default_provider="anthropic",
        default_model_id="claude-sonnet-4-5",
    )
    ts_turn_2._effective_settings = empty_settings()

    # Turn 1 sends with sequential tool_call_id="tool-call:0"
    r1 = ts_turn_1.invoke(
        "manage_session",
        {"action": "send_message", "conversation_id": code_cid, "message": "Fase 1"},
        call_id="tool-call:0",
    )
    assert r1.status == "succeeded", r1.content

    # Retry within the SAME Friend turn deduplicates cleanly
    r1_retry = ts_turn_1.invoke(
        "manage_session",
        {"action": "send_message", "conversation_id": code_cid, "message": "Fase 1"},
        call_id="tool-call:0",
    )
    assert r1_retry.status == "succeeded", r1_retry.content
    assert r1_retry.payload["turn_id"] == r1.payload["turn_id"]

    # Turn 2 ALSO emits sequential "tool-call:0" with a different message -> must NOT collide!
    r2 = ts_turn_2.invoke(
        "manage_session",
        {"action": "send_message", "conversation_id": code_cid, "message": "Fase 2"},
        call_id="tool-call:0",
    )
    assert r2.status == "succeeded", r2.content

    # Direct deliver_to_session with explicit identical idempotency_key on earlier turn but DIFFERENT text must fail
    with pytest.raises(ApplicationValidationError, match="different message"):
        deliver_to_session(
            chat_store=store,
            user_id="u1",
            conversation_id=code_cid,
            text="Texto totalmente diferente",
            delivery="queue",
            idempotency_key=f"friend:{friend_cid}:friend-turn-1:tool-call:0",
            provider="anthropic",
            model_id="claude-sonnet-4-5",
            permission_mode="accept_edits",
            code_mode="code",
            settings=empty_settings(),
        )


# ---------------------------------------------------------------------------
# Finding #9 [LOW]: _build_initial_user_content losing reminders on 85% pre-compact
# and premature set_permission_mode in manage_session
# ---------------------------------------------------------------------------


def test_redteam_09_build_initial_user_content_preserves_reminders_on_85_pre_compaction(
    tmp_path: Path,
) -> None:
    """When _build_initial_user_content triggers 85% pre-compaction recursion, drained
    pending_reminders must NOT be discarded and on_reminder_ack must still fire."""
    acked: list[str] = []
    reminders = ReminderQueue()
    reminders.add("user_message", "Diretriz crítica do usuário", key="user-msg:ctrl-precompact-1")

    class _MiniStore:
        def record(self, *args: Any, **kwargs: Any) -> None:
            pass

        def lifecycle(self, *args: Any, **kwargs: Any) -> None:
            pass

    rt = AgenticTurnRuntime(
        store=_MiniStore(),  # type: ignore[arg-type]
        provider=object(),  # type: ignore[arg-type]
        toolset=AgentToolset(ConversationWorkspace(tmp_path, "c-precompact")),
        limits=AgenticLimits(
            max_iterations=5,
            max_actions=10,
            deadline=timedelta(minutes=5),
            context_window_tokens=1000,
        ),
        reminders=reminders,
        on_reminder_ack=acked.append,
    )
    compacted_calls: list[int] = []

    def _fake_compact(messages: list[dict[str, object]], turn: dict[str, object]) -> None:
        compacted_calls.append(1)
        # Shrink prior messages so recursive call fits easily
        messages[:] = [{"role": "user", "content": "Pergunta atual"}]

    rt._compact_keeping_recent = _fake_compact  # type: ignore[method-assign]

    messages: list[dict[str, object]] = [
        {"role": "user", "content": "X" * 6000},
        {"role": "assistant", "content": "Y" * 6000},
        {"role": "user", "content": "Pergunta atual"},
    ]
    from datetime import UTC, datetime

    runner = NativeTurnRunner(
        runtime=rt,
        provider=object(),  # type: ignore[arg-type]
        turn={"turn_id": "t-pc", "conversation_id": "c-precompact"},
        turn_id="t-pc",
        messages=messages,
        deadline=datetime.now(UTC) + timedelta(minutes=5),
    )

    blocks = runner._build_initial_user_content(tool_schemas=[])
    assert compacted_calls == [1], "Expected 85% pre-compaction to trigger recursion"
    combined_text = "\n".join(str(b.get("text") or "") for b in blocks)
    assert "Diretriz crítica do usuário" in combined_text, combined_text
    assert acked == ["ctrl-precompact-1"], acked


def test_redteam_09_manage_session_does_not_overwrite_permission_mode_before_model_change_fallback(
    tmp_path: Path,
) -> None:
    """When manage_session(action='send_message', permission_mode=...) targets a live running
    session, set_permission_mode must NOT be called prematurely before _deliver_session_message
    decides model_change fallback (and a rejected delivery must leave DB permission_mode untouched)."""
    engine = _make_engine()
    store = PostgresChatStore(engine)
    friend_cid, code_cid, _live_tid, _claimed = _seed_friend_and_code(store, live=True)

    set_perm_calls: list[str] = []
    orig_set_perm = store.set_permission_mode

    def _spy_set_perm(cid: str, uid: str, mode: str):
        set_perm_calls.append(mode)
        return orig_set_perm(cid, uid, mode)

    store.set_permission_mode = _spy_set_perm  # type: ignore[method-assign]

    toolset = AgentToolset(
        ConversationWorkspace.at_root(tmp_path),
        chat_store=store,
        project_user_id="u1",
        conversation_id=friend_cid,
        turn_id="friend-perm-1",
        default_provider="anthropic",
        default_model_id="claude-sonnet-4-5",
    )
    toolset._effective_settings = empty_settings()

    # 1) A failed delivery must NOT have prematurely mutated conversations.permission_mode
    bad = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": code_cid,
            "message": "Teste rejeitado.",
            "delivery": "invalid_delivery_mode",
            "permission_mode": "full_autonomy",
        },
        call_id="rt09-perm-bad",
    )
    assert bad.status == "failed"
    assert set_perm_calls == []
    with engine.connect() as conn:
        perm_after_bad = conn.execute(
            select(conversations.c.permission_mode).where(conversations.c.conversation_id == code_cid)
        ).scalar_one()
    assert perm_after_bad == "accept_edits"

    # 2) Valid delivery with permission_mode change on live turn detects model_change cleanly
    #    without calling set_permission_mode prematurely before _deliver_session_message
    outcome = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": code_cid,
            "message": "Depois rode com autonomia total.",
            "permission_mode": "full_autonomy",
        },
        call_id="rt09-perm-1",
    )
    assert outcome.status == "succeeded", outcome.content
    assert outcome.payload["delivery"] == "queued_next_turn"
    assert outcome.payload["delivery_fallback"] == "model_change"
    assert set_perm_calls == [], f"set_permission_mode was called prematurely: {set_perm_calls}"
