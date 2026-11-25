"""A turn stopped inside ask_user takes a plain follow-up as its answer (chat_7dcd6dde, 25/11/2026).

The leader called exit_plan_mode in full autonomy, kept going and asked with ask_user. The page treated the plan
event as the end of the turn and sent each answer as a new message: three turns queued behind the one waiting for
the answer, which waited until its deadline while the session looked busy.
"""
from __future__ import annotations

from datetime import UTC, datetime
from itertools import count


from sqlalchemy import create_engine, insert, select

from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.schema import conversation_activity_events, conversation_turns, metadata

_ids = count(1)


def _store() -> PostgresChatStore:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def _running_turn(store: PostgresChatStore) -> dict:
    receipt = store.create(user_id="u", message="planeje", provider="google-antigravity", model_id="m", idempotency_key="k1")
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    return turn


def _event(store: PostgresChatStore, turn: dict, event_type: str, payload: dict | None = None, *, sub: bool = False) -> None:
    sequence = next(_ids)
    with store._engine.begin() as connection:
        connection.execute(insert(conversation_activity_events).values(
            event_id=f"activity:{turn['turn_id']}:{sequence}",
            conversation_id=str(turn["conversation_id"]),
            user_id="u",
            workspace_id=None,
            turn_id=str(turn["turn_id"]),
            execution_id=str(turn.get("execution_id") or "exe"),
            agent_id="agent:sub" if sub else "agent:main",
            parent_agent_id="agent:main" if sub else None,
            event_type=event_type,
            sequence=sequence,
            summary=event_type,
            payload=payload or {},
            visibility="public",
            created_at=datetime.now(UTC),
        ))


def _asking(store: PostgresChatStore, turn: dict) -> None:
    _event(store, turn, "tool.finished", {"tool_name": "exit_plan_mode", "plan_ready": True, "plan_approval": True, "plan_mode": False})
    _event(store, turn, "tool.started", {"tool_name": "ask_user"})
    _event(store, turn, "tool.finished", {"tool_name": "ask_user", "questions": [{"id": "q1"}]})
    _event(store, turn, "turn.waiting_user", {"state": "waiting_user_in_turn", "in_turn": True})


def test_live_question_turn_finds_the_turn_waiting_inside_ask_user() -> None:
    store = _store()
    turn = _running_turn(store)
    _asking(store, turn)
    # A subagent event or a heartbeat after the question does not mean the turn read an answer.
    _event(store, turn, "tool.finished", {"tool_name": "read_file"}, sub=True)
    _event(store, turn, "turn.started", {"waiting_model": True})
    live = ChatApplication(store).live_question_turn(str(turn["conversation_id"]), "u")
    assert live is not None
    assert live["turn_id"] == turn["turn_id"]
    assert live["message_id"] == turn["assistant_message_id"]
    assert live["state"] == "running"


def test_no_live_question_turn_when_the_turn_moved_on_or_waits_classically() -> None:
    store = _store()
    turn = _running_turn(store)
    app = ChatApplication(store)
    conversation_id = str(turn["conversation_id"])
    assert app.live_question_turn(conversation_id, "u") is None
    _event(store, turn, "turn.waiting_user", {"state": "waiting_user"})
    assert app.live_question_turn(conversation_id, "u") is None
    _asking(store, turn)
    _event(store, turn, "tool.started", {"tool_name": "read_file"})
    assert app.live_question_turn(conversation_id, "u") is None
    assert app.live_question_turn(conversation_id, "someone-else") is None


def test_no_live_question_turn_once_the_turn_ended() -> None:
    store = _store()
    turn = _running_turn(store)
    _asking(store, turn)
    store.finish(turn)
    assert ChatApplication(store).live_question_turn(str(turn["conversation_id"]), "u") is None


class _Route:
    """Only what `_deliver_to_waiting_question` reads from the request payload."""

    def __init__(self, message: str, attachments: list[str] | None = None, cli: object | None = None) -> None:
        self.message = message
        self.attachments = attachments or []
        self.cli = cli


def test_post_message_to_a_turn_waiting_inside_ask_user_goes_to_its_queue() -> None:
    from agentos.api import gateway

    store = _store()
    turn = _running_turn(store)
    _asking(store, turn)
    app = ChatApplication(store)
    conversation_id = str(turn["conversation_id"])

    receipt = gateway.deliver_to_waiting_question(app, "u", conversation_id, _Route("Respostas às perguntas do agente:\n\nSeleção: A"), "idem-1")
    assert receipt is not None
    assert receipt["queued"] is True and receipt["queue_id"]
    assert receipt["turn_id"] == turn["turn_id"]
    assert {"conversation_id", "title", "turn_id", "message_id", "state", "mode", "permission_mode"} <= set(receipt)
    assert [item["content"] for item in store.queued_inputs(conversation_id, "u")] == ["Respostas às perguntas do agente:\n\nSeleção: A"]
    with store._engine.connect() as connection:
        turns = connection.execute(select(conversation_turns.c.turn_id).where(conversation_turns.c.conversation_id == conversation_id)).all()
    assert len(turns) == 1, "no new turn queued behind the asking one"


def test_post_message_keeps_the_normal_path_for_commands_attachments_and_other_turns() -> None:
    from agentos.api import gateway

    store = _store()
    turn = _running_turn(store)
    app = ChatApplication(store)
    conversation_id = str(turn["conversation_id"])
    assert gateway.deliver_to_waiting_question(app, "u", conversation_id, _Route("oi"), "idem-0") is None
    _asking(store, turn)
    assert gateway.deliver_to_waiting_question(app, "u", conversation_id, _Route("/compact"), "idem-2") is None
    assert gateway.deliver_to_waiting_question(app, "u", conversation_id, _Route("!ls"), "idem-3") is None
    assert gateway.deliver_to_waiting_question(app, "u", conversation_id, _Route("veja", ["img.png"]), "idem-4") is None
    assert gateway.deliver_to_waiting_question(app, "u", conversation_id, _Route("oi", cli=object()), "idem-5") is None
    assert store.queued_inputs(conversation_id, "u") == []
