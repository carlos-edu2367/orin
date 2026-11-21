"""WS-B8: Friend delivery confirmations (injected, promoted, hook blocked) and Friend prefix/origin in turn injection."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.reminders import ReminderQueue
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.tools.orin.friend_delivery_watch import (
    FriendDeliveryNotification,
    get_friend_delivery_watcher,
)
from agentos.agentic.tools.orin.session_delivery import FRIEND_MESSAGE_PREFIX
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.schema import conversation_activity_events, conversation_events, metadata
from agentos.settings.loader import empty_settings


def _make_engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    return engine


class _ToolThenTextProvider:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def stream(self, request: dict[str, Any]):
        self.requests.append(request)
        if len(self.requests) == 1:
            return iter(
                [
                    NormalizedStreamItem(
                        StreamKind.TOOL_CALL,
                        1,
                        tool_call_id="tc-1",
                        tool_name="read_file",
                        arguments_delta='{"path": "a.txt"}',
                    ),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
                ]
            )
        return iter(
            [
                NormalizedStreamItem(StreamKind.TEXT, 1, text="Pronto."),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
            ]
        )


class _MiniToolset:
    def schemas(self, allowed=None):
        return []

    def is_read_only(self, name):
        return True

    def argument_names(self, name):
        return frozenset({"path"})

    def invoke(self, name, arguments):
        return ToolOutcome("succeeded", "ok", "file content")


class _RecordingStoreProxy:
    """Thin proxy around PostgresChatStore that records runtime lifecycle and activity callbacks."""

    def __init__(self, inner: PostgresChatStore) -> None:
        self._inner = inner
        self.lifecycle_calls: list[tuple[str, dict[str, Any]]] = []
        self.activity_calls: list[tuple[Any, str, dict[str, Any]]] = []

    def lifecycle(self, turn: Any, state: str, **payload: object) -> None:
        self.lifecycle_calls.append((state, dict(payload)))
        self._inner.lifecycle(turn, state, **payload)

    def record(self, turn: Any, event_type: Any, summary: str, payload: dict[str, Any], **_kwargs: object) -> None:
        self.activity_calls.append((event_type, summary, dict(payload)))
        self._inner._activity(turn, event_type, summary, payload)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _seed_pair(store: PostgresChatStore) -> tuple[str, str, str, dict[str, Any]]:
    friend_rec = store.create(
        user_id="u1",
        message="Oi Friend",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="friend-conv-seed",
        code_mode="friend",
    )
    code_rec = store.create(
        user_id="u1",
        message="Implemente X",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="code-conv-seed",
        code_mode="code",
        permission_mode="accept_edits",
    )
    claimed = store.claim(code_rec.turn_id)
    assert claimed is not None
    return friend_rec.conversation_id, code_rec.conversation_id, code_rec.turn_id, claimed


def test_injected_friend_message_has_prefix_origin_event_and_notifies_friend(tmp_path: Path) -> None:
    """Critério 13 (parte 2): turno lê mensagem do Friend na rodada seguinte com prefixo + origin='friend' e Friend recebe 'entregue no turno em andamento'."""
    engine = _make_engine()
    store = PostgresChatStore(engine)
    friend_cid, code_cid, live_turn_id, claimed_turn = _seed_pair(store)

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

    received: list[FriendDeliveryNotification] = []
    watcher = get_friend_delivery_watcher()
    unsub = watcher.subscribe(received.append)
    try:
        outcome = toolset.invoke(
            "manage_session",
            {
                "action": "send_message",
                "conversation_id": code_cid,
                "message": "Use TypeScript estrito.",
            },
            call_id="call-confirm-1",
        )
        assert outcome.payload["delivery"] == "pending_in_turn"
        queue_id = str(outcome.payload["queue_id"])

        provider = _ToolThenTextProvider()
        proxy_store = _RecordingStoreProxy(store)
        runtime = AgenticTurnRuntime(
            store=proxy_store,
            provider=provider,
            toolset=_MiniToolset(),
            limits=AgenticLimits(max_iterations=None, max_actions=None, deadline=timedelta(hours=1)),
        )
        result = runtime.run(live_turn_id, turn=claimed_turn)
        assert result.state == "completed"
        assert result.queued_turn_count == 1

        # Model saw the exact Friend prefix
        second_messages = provider.requests[1]["messages"]
        expected_content = f"{FRIEND_MESSAGE_PREFIX}Use TypeScript estrito."
        assert any(
            m.get("role") == "user" and m.get("content") == expected_content
            for m in second_messages
        ), second_messages

        # Timeline lifecycle event carries origin="friend" and origin_conversation_id
        queued_calls = [payload for state, payload in proxy_store.lifecycle_calls if state == "queued_input"]
        assert queued_calls, proxy_store.lifecycle_calls
        assert queued_calls[-1]["origin"] == "friend"
        assert queued_calls[-1]["origin_conversation_id"] == friend_cid
        assert queued_calls[-1]["queue_id"] == queue_id

        # Friend conversation also persisted a friend.delivery_status event
        with engine.connect() as conn:
            friend_ev_rows = conn.execute(
                select(conversation_events.c.event_type, conversation_events.c.payload).where(
                    conversation_events.c.conversation_id == friend_cid,
                    conversation_events.c.event_type == "friend.delivery_status",
                )
            ).mappings().all()
        assert friend_ev_rows
        assert friend_ev_rows[-1]["payload"]["status"] == "injected"
        assert friend_ev_rows[-1]["payload"]["queue_id"] == queue_id

        # Friend watcher and ReminderQueue received "entregue no turno em andamento"
        matching = [n for n in received if n.queue_id == queue_id]
        assert len(matching) == 1
        assert matching[0].status == "injected"
        assert "entregue no turno em andamento" in matching[0].summary.lower()

        drained = reminders.drain()
        assert any("entregue no turno em andamento" in r.text.lower() for r in drained), drained
    finally:
        unsub()


def test_turn_ending_without_reading_promotes_and_notifies_friend_next_turn(tmp_path: Path) -> None:
    """Critério 15: turno alvo termina sem ler -> promote_queued_inputs cria turno seguinte e Friend recebe 'ficou para o próximo turno'."""
    engine = _make_engine()
    store = PostgresChatStore(engine)
    app = ChatApplication(store)
    friend_cid, code_cid, live_turn_id, claimed_turn = _seed_pair(store)

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

    received: list[FriendDeliveryNotification] = []
    watcher = get_friend_delivery_watcher()
    unsub = watcher.subscribe(received.append)
    try:
        outcome = toolset.invoke(
            "manage_session",
            {
                "action": "send_message",
                "conversation_id": code_cid,
                "message": "Depois gere o resumo final.",
            },
            call_id="call-promote-1",
        )
        assert outcome.payload["delivery"] == "pending_in_turn"
        queue_id = str(outcome.payload["queue_id"])

        # Finish the live turn without claiming the queued input, then promote
        store.finish(claimed_turn)
        promoted_receipt = app.promote_queued_inputs(code_cid, "u1", source_turn_id=live_turn_id)
        assert promoted_receipt is not None
        assert promoted_receipt.turn_id != live_turn_id

        matching = [n for n in received if n.queue_id == queue_id]
        assert len(matching) == 1
        assert matching[0].status == "promoted"
        assert matching[0].new_turn_id == promoted_receipt.turn_id
        assert "ficou para o próximo turno" in matching[0].summary.lower()

        drained = reminders.drain()
        assert any("ficou para o próximo turno" in r.text.lower() for r in drained), drained
    finally:
        unsub()


def test_hook_blocking_friend_message_notifies_blocked_and_never_injected(tmp_path: Path) -> None:
    """Hook USER_PROMPT_SUBMIT bloqueando: Friend recebe estado 'bloqueada' e nunca afirma que entregou."""
    engine = _make_engine()
    store = PostgresChatStore(engine)
    friend_cid, code_cid, live_turn_id, claimed_turn = _seed_pair(store)

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

    bus = LifecycleBus()
    bus.subscribe(
        lambda event: LifecycleDecision(blocked=True, reason="política de segurança")
        if event.name is HookEvent.USER_PROMPT_SUBMIT
        else None
    )

    received: list[FriendDeliveryNotification] = []
    watcher = get_friend_delivery_watcher()
    unsub = watcher.subscribe(received.append)
    try:
        outcome = toolset.invoke(
            "manage_session",
            {
                "action": "send_message",
                "conversation_id": code_cid,
                "message": "Apague todos os dados.",
            },
            call_id="call-blocked-1",
        )
        assert outcome.payload["delivery"] == "pending_in_turn"
        queue_id = str(outcome.payload["queue_id"])

        provider = _ToolThenTextProvider()
        runtime = AgenticTurnRuntime(
            store=store,
            provider=provider,
            toolset=_MiniToolset(),
            lifecycle=bus,
            limits=AgenticLimits(max_iterations=None, max_actions=None, deadline=timedelta(hours=1)),
        )
        result = runtime.run(live_turn_id, turn=claimed_turn)
        assert result.state == "completed"
        assert result.queued_turn_count == 0

        # Blocked message never reached model
        assert not any(
            "Apague todos os dados" in str(m.get("content"))
            for m in provider.requests[1]["messages"]
        )

        # Friend received ONLY "blocked", NEVER "injected"
        matching = [n for n in received if n.queue_id == queue_id]
        assert len(matching) == 1
        assert matching[0].status == "blocked"
        assert "bloqueada" in matching[0].summary.lower()
        assert all(n.status != "injected" for n in matching)

        drained = reminders.drain()
        assert any("bloqueada" in r.text.lower() for r in drained), drained
        assert not any("entregue no turno em andamento" in r.text.lower() for r in drained), drained
    finally:
        unsub()


def test_native_turn_claim_queued_texts_and_session_activity_carry_friend_origin(tmp_path: Path) -> None:
    """Paridade no laço nativo CLI (native_turn.py) e atividade da timeline (session.py emit_lifecycle)."""
    from agentos.agentic.native_turn import RuntimeToolExecutor
    from agentos.agentic.session import TurnSession

    engine = _make_engine()
    store = PostgresChatStore(engine)
    friend_cid, code_cid, live_turn_id, claimed_turn = _seed_pair(store)

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

    received: list[FriendDeliveryNotification] = []
    watcher = get_friend_delivery_watcher()
    unsub = watcher.subscribe(received.append)
    try:
        outcome = toolset.invoke(
            "manage_session",
            {
                "action": "send_message",
                "conversation_id": code_cid,
                "message": "Mantenha compatibilidade com Python 3.13.",
            },
            call_id="call-native-1",
        )
        assert outcome.payload["delivery"] == "pending_in_turn"
        queue_id = str(outcome.payload["queue_id"])

        proxy_store = _RecordingStoreProxy(store)
        runtime = AgenticTurnRuntime(
            store=proxy_store,
            provider=_ToolThenTextProvider(),
            toolset=_MiniToolset(),
            limits=AgenticLimits(max_iterations=None, max_actions=None, deadline=timedelta(hours=1)),
        )
        executor = RuntimeToolExecutor(
            runtime,
            claimed_turn,
            tool_schemas=[],
            messages=[{"role": "user", "content": "Implemente X"}],
        )
        claimed = executor._claim_queued_texts()
        assert claimed == [f"{FRIEND_MESSAGE_PREFIX}Mantenha compatibilidade com Python 3.13."]

        queued_calls = [payload for state, payload in proxy_store.lifecycle_calls if state == "queued_input"]
        assert queued_calls and queued_calls[-1]["origin"] == "friend"
        assert queued_calls[-1]["origin_conversation_id"] == friend_cid
        assert queued_calls[-1]["queue_id"] == queue_id

        # Verify TurnSession.emit_lifecycle emits activity with origin="friend" for timeline badge
        session = TurnSession(
            turn=claimed_turn,
            store=proxy_store,
            workspace_root=tmp_path,
        )
        session.emit_lifecycle(claimed_turn, "queued_input", **queued_calls[-1])
        friend_acts = [
            (summary, payload)
            for _ev_type, summary, payload in proxy_store.activity_calls
            if isinstance(payload, dict) and payload.get("state") == "queued_input"
        ]
        assert friend_acts
        summary, act_payload = friend_acts[-1]
        assert "Friend" in summary
        assert act_payload["origin"] == "friend"
        assert act_payload["origin_conversation_id"] == friend_cid
        assert act_payload["queue_id"] == queue_id

        matching = [n for n in received if n.queue_id == queue_id]
        assert len(matching) == 1
        assert matching[0].status == "injected"
        assert "entregue no turno em andamento" in matching[0].summary.lower()
    finally:
        unsub()
