"""WS-B7: AgentControlStore & InMemoryAgentControls state transitions, sender fields, delivered_at and list_for_agent."""
from __future__ import annotations

from datetime import UTC, datetime
import threading

import pytest
from sqlalchemy import select

from agentos.api.contracts import ApplicationValidationError
from agentos.conversations.agent_controls import AgentControlStore, InMemoryAgentControls
from agentos.persistence.postgres.schema import conversation_agent_controls, create_engine_for_tests


def _make_store(kind: str):
    engine = create_engine_for_tests()
    store = AgentControlStore(engine) if kind == "sql" else InMemoryAgentControls()
    return engine, store


@pytest.mark.parametrize("kind", ["sql", "memory"])
def test_post_message_supports_sender_kind_and_sender_agent_id(kind: str) -> None:
    engine, store = _make_store(kind)
    try:
        user_id = "u1"
        conv_id = "conv-1"
        agent_id = "agent:conv-1:child-1"

        c_user = store.post_message(user_id, conv_id, agent_id, "mensagem do usuario")
        c_parent = store.post_message(
            user_id,
            conv_id,
            agent_id,
            "pare de editar X",
            sender_kind="parent",
            sender_agent_id="agent:conv-1:main",
        )

        with pytest.raises(ApplicationValidationError):
            store.post_message(user_id, conv_id, agent_id, "oi", sender_kind="unknown")

        items = store.list_for_agent(user_id, conv_id, agent_id)
        assert [item["control_id"] for item in items] == [c_user, c_parent]
        assert items[0]["sender_kind"] == "user"
        assert items[0]["sender_agent_id"] is None
        assert items[0]["state"] == "queued"
        assert items[0]["delivered_at"] is None
        assert items[0]["message"] == "mensagem do usuario"
        assert items[0]["preview"] == "mensagem do usuario"

        assert items[1]["sender_kind"] == "parent"
        assert items[1]["sender_agent_id"] == "agent:conv-1:main"
        assert items[1]["state"] == "queued"
        assert items[1]["delivered_at"] is None

        taken = store.take(user_id, conv_id, agent_id)
        assert len(taken) == 2
        assert taken[0]["sender_kind"] == "user"
        assert taken[0]["sender_agent_id"] is None
        assert taken[1]["sender_kind"] == "parent"
        assert taken[1]["sender_agent_id"] == "agent:conv-1:main"
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["sql", "memory"])
def test_state_transitions_queued_injected_delivered_with_utc_delivered_at(kind: str) -> None:
    engine, store = _make_store(kind)
    try:
        agent_id = "agent:conv-1:child-2"
        cid = store.post_message("u1", "conv-1", agent_id, "foca no parser")

        before = store.list_for_agent("u1", "conv-1", agent_id)
        assert before[0]["state"] == "queued"
        assert before[0]["delivered_at"] is None

        store.take("u1", "conv-1", agent_id)
        store.set_outcome(cid, "injected")
        injected = store.list_for_agent("u1", "conv-1", agent_id)
        assert injected[0]["state"] == "injected"
        assert injected[0]["delivered_at"] is None

        first_mark = store.mark_delivered(cid)
        assert first_mark is True
        delivered = store.list_for_agent("u1", "conv-1", agent_id)
        assert delivered[0]["state"] == "delivered"
        delivered_at = delivered[0]["delivered_at"]
        assert isinstance(delivered_at, datetime)
        assert delivered_at.tzinfo is not None

        # Idempotent: repeat mark_delivered returns False and preserves first delivered_at.
        second_mark = store.mark_delivered(cid, delivered_at=datetime(2099, 1, 1, tzinfo=UTC))
        assert second_mark is False
        again = store.list_for_agent("u1", "conv-1", agent_id)
        assert again[0]["delivered_at"] == delivered_at

        if kind == "sql":
            with engine.connect() as conn:
                row = conn.execute(
                    select(conversation_agent_controls).where(
                        conversation_agent_controls.c.control_id == cid
                    )
                ).mappings().one()
            assert row["outcome"] == "delivered"
            assert row["delivered_at"] is not None
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["sql", "memory"])
def test_set_outcome_delivered_also_sets_delivered_at_and_supports_other_outcomes(kind: str) -> None:
    engine, store = _make_store(kind)
    try:
        agent_id = "agent:conv-1:child-3"
        c_del = store.post_message("u1", "conv-1", agent_id, "m1")
        c_fwd = store.post_message("u1", "conv-1", agent_id, "m2")
        c_exp = store.post_message("u1", "conv-1", agent_id, "m3")
        store.request_stop("u1", "conv-1", agent_id)  # stop controls must not appear in list_for_agent

        store.set_outcome(c_del, "delivered")
        store.set_outcome(c_fwd, "forwarded")
        store.set_outcome(c_exp, "expired")

        items = store.list_for_agent("u1", "conv-1", agent_id)
        assert [item["control_id"] for item in items] == [c_del, c_fwd, c_exp]
        assert [item["state"] for item in items] == ["delivered", "forwarded", "expired"]
        assert items[0]["delivered_at"] is not None
        assert items[1]["delivered_at"] is None
        assert items[2]["delivered_at"] is None
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["sql", "memory"])
def test_list_for_agent_isolates_by_user_conversation_and_agent_and_truncates_preview(kind: str) -> None:
    engine, store = _make_store(kind)
    try:
        long_msg = "A" * 260
        c1 = store.post_message("u1", "conv-1", "agent-a", long_msg)
        c2 = store.post_message("u1", "conv-1", "agent-a", "segunda")
        store.post_message("u1", "conv-1", "agent-b", "outro agente")
        store.post_message("u2", "conv-1", "agent-a", "outro usuario")
        store.post_message("u1", "conv-2", "agent-a", "outra conversa")

        items = store.list_for_agent("u1", "conv-1", "agent-a")
        assert [item["control_id"] for item in items] == [c1, c2]
        assert items[0]["message"] == long_msg
        assert len(str(items[0]["preview"])) == 200
        assert items[1]["message"] == "segunda"
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["sql", "memory"])
def test_take_vs_withdraw_barrier_single_winner(kind: str, tmp_path) -> None:
    """Concurrent take() and withdraw() on the same control must have exactly one winner."""
    from agentos.persistence.postgres.schema import metadata
    from agentos.persistence.sqlite import create_local_engine

    if kind == "sql":
        engine = create_local_engine(f"sqlite:///{(tmp_path / 'race.db').as_posix()}")
        metadata.create_all(engine)
        store: AgentControlStore | InMemoryAgentControls = AgentControlStore(engine)
    else:
        engine = None
        store = InMemoryAgentControls()
    try:
        agent_id = "agent:conv-1:race"
        cid = store.post_message("u1", "conv-1", agent_id, "race test")
        barrier = threading.Barrier(2)
        results: dict[str, object] = {}

        def _taker() -> None:
            barrier.wait()
            results["taken"] = store.take("u1", "conv-1", agent_id)

        def _withdrawer() -> None:
            barrier.wait()
            results["withdrawn"] = store.withdraw(cid)

        t1 = threading.Thread(target=_taker)
        t2 = threading.Thread(target=_withdrawer)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        taken_list = results["taken"]
        withdrawn = bool(results["withdrawn"])
        assert isinstance(taken_list, list)
        # Exactly one succeeded in claiming the row.
        assert (len(taken_list) == 1) != withdrawn
    finally:
        if engine is not None:
            engine.dispose()
