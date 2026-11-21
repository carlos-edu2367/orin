"""WS-B8: origin/origin_conversation_id on conversation_input_queue, defer-to-next-turn route, and respond preservation."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.api.contracts import ApplicationConflictError, ApplicationNotFoundError
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.schema import conversation_input_queue, metadata


def _make_engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    return engine


def _seed_live_conversation(store: PostgresChatStore, user_id: str = "u1") -> tuple[str, str]:
    receipt = store.create(
        user_id=user_id,
        message="Implemente o módulo X",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="seed-turn-1",
        code_mode="code",
        permission_mode="accept_edits",
    )
    claimed = store.claim(receipt.turn_id)
    assert claimed is not None
    return receipt.conversation_id, receipt.turn_id


def test_enqueue_input_persists_and_returns_origin_and_origin_conversation_id() -> None:
    engine = _make_engine()
    store = PostgresChatStore(engine)
    cid, turn_id = _seed_live_conversation(store)

    item = store.enqueue_input(
        user_id="u1",
        conversation_id=cid,
        content="Foque nos testes primeiro",
        idempotency_key="friend:call-origin-1",
        origin="friend",
        origin_conversation_id="conv-friend-99",
    )
    assert item["origin"] == "friend"
    assert item["origin_conversation_id"] == "conv-friend-99"
    assert item["state"] == "pending"

    with engine.connect() as conn:
        row = conn.execute(
            select(
                conversation_input_queue.c.origin,
                conversation_input_queue.c.origin_conversation_id,
            ).where(conversation_input_queue.c.queue_id == item["queue_id"])
        ).mappings().one()
    assert row["origin"] == "friend"
    assert row["origin_conversation_id"] == "conv-friend-99"

    listed = store.queued_inputs(cid, "u1")
    assert len(listed) == 1
    assert listed[0]["origin"] == "friend"
    assert listed[0]["origin_conversation_id"] == "conv-friend-99"

    claimed = store.claim_queued_inputs(
        {"conversation_id": cid, "user_id": "u1", "turn_id": turn_id}
    )
    assert len(claimed) == 1
    assert claimed[0]["queue_id"] == item["queue_id"]
    assert claimed[0]["origin"] == "friend"
    assert claimed[0]["origin_conversation_id"] == "conv-friend-99"


def test_chat_application_enqueue_accepts_origin_and_defaults_to_user() -> None:
    engine = _make_engine()
    store = PostgresChatStore(engine)
    app = ChatApplication(store)
    cid, _turn_id = _seed_live_conversation(store)

    default_res = app.enqueue("u1", cid, "mensagem direta do usuário", "k-user-1")
    assert default_res["queued"] is True
    assert default_res["origin"] == "user"
    assert default_res["origin_conversation_id"] is None

    friend_res = app.enqueue(
        "u1",
        cid,
        "mensagem enviada via Friend",
        "k-friend-1",
        origin="friend",
        origin_conversation_id="conv-friend-1",
    )
    assert friend_res["queued"] is True
    assert friend_res["origin"] == "friend"
    assert friend_res["origin_conversation_id"] == "conv-friend-1"


def test_defer_queued_input_to_next_turn_cancels_pending_and_creates_queued_turn() -> None:
    engine = _make_engine()
    store = PostgresChatStore(engine)
    app = ChatApplication(store)
    cid, live_turn_id = _seed_live_conversation(store)

    queued = store.enqueue_input(
        user_id="u1",
        conversation_id=cid,
        content="Faça também o changelog",
        idempotency_key="friend:defer-1",
        origin="friend",
        origin_conversation_id="conv-friend-42",
    )

    result = app.defer_queued_input_to_next_turn("u1", cid, str(queued["queue_id"]))
    assert result["deferred"] is True
    assert result["queue_id"] == queued["queue_id"]
    assert result["delivery"] == "queued_next_turn"
    assert result["turn_id"] != live_turn_id
    assert result["state"] == "queued"

    # Original queue row is cancelled and no longer pending
    assert store.queued_inputs(cid, "u1") == []
    with engine.connect() as conn:
        state = conn.execute(
            select(conversation_input_queue.c.state).where(
                conversation_input_queue.c.queue_id == queued["queue_id"]
            )
        ).scalar_one()
    assert state == "cancelled"

    # A new turn was created with the deferred text
    conv = store.get(cid, "u1")
    assert len(conv["turns"]) == 2
    assert conv["turns"][-1]["turn_id"] == result["turn_id"]
    assert any(m["content"] == "Faça também o changelog" for m in conv["messages"])


def test_defer_queued_input_raises_conflict_when_already_injected() -> None:
    engine = _make_engine()
    store = PostgresChatStore(engine)
    app = ChatApplication(store)
    cid, live_turn_id = _seed_live_conversation(store)

    queued = store.enqueue_input(
        user_id="u1",
        conversation_id=cid,
        content="Ajuste a doc",
        origin="friend",
        origin_conversation_id="conv-friend-42",
    )
    store.claim_queued_inputs({"conversation_id": cid, "user_id": "u1", "turn_id": live_turn_id})

    with pytest.raises(ApplicationConflictError):
        app.defer_queued_input_to_next_turn("u1", cid, str(queued["queue_id"]))

    with pytest.raises(ApplicationNotFoundError):
        app.defer_queued_input_to_next_turn("u1", cid, "queue-does-not-exist")


def test_http_defer_route_returns_200_for_pending_and_409_for_injected() -> None:
    async def _run() -> None:
        engine = _make_engine()
        store = PostgresChatStore(engine)
        chat_app = ChatApplication(store)
        cid, live_turn_id = _seed_live_conversation(store, user_id="user-1")

        security = InMemorySecurityService()
        security.add_pat("pat", AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})))
        api_app = create_app(ApiServices(security=security, conversation_application=chat_app))
        headers = {"Authorization": "Bearer pat"}

        q_pending = store.enqueue_input(
            user_id="user-1",
            conversation_id=cid,
            content="Deixe para o próximo turno",
            origin="friend",
            origin_conversation_id="conv-friend-http",
        )
        q_injected = store.enqueue_input(
            user_id="user-1",
            conversation_id=cid,
            content="Já lida pelo turno",
            origin="friend",
            origin_conversation_id="conv-friend-http",
        )

        transport = ASGITransport(app=api_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp_ok = await client.post(
                f"/v1/conversations/{cid}/queue/{q_pending['queue_id']}/defer",
                headers=headers,
            )
            assert resp_ok.status_code == 200, resp_ok.text
            body = resp_ok.json()
            assert body["deferred"] is True
            assert body["delivery"] == "queued_next_turn"
            assert body["turn_id"] != live_turn_id

            # Now claim q_injected so it becomes injected -> defer must return 409
            store.claim_queued_inputs(
                {"conversation_id": cid, "user_id": "user-1", "turn_id": live_turn_id}
            )
            resp_conflict = await client.post(
                f"/v1/conversations/{cid}/queue/{q_injected['queue_id']}/defer",
                headers=headers,
            )
            assert resp_conflict.status_code == 409, resp_conflict.text

    asyncio.run(_run())


def test_manage_session_respond_on_live_turn_is_preserved(tmp_path: Path) -> None:
    engine = _make_engine()
    store = PostgresChatStore(engine)
    cid, live_turn_id = _seed_live_conversation(store, user_id="u1")

    friend = store.create(
        user_id="u1",
        message="Oi Friend",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="friend-seed",
        code_mode="friend",
    )
    toolset = AgentToolset(
        ConversationWorkspace.at_root(tmp_path),
        chat_store=store,
        project_user_id="u1",
        conversation_id=friend.conversation_id,
        default_provider="anthropic",
        default_model_id="claude-sonnet-4-5",
    )

    outcome = toolset.manage_session(
        action="respond",
        conversation_id=cid,
        answers="30 dias",
    )
    assert outcome.status == "succeeded"
    assert outcome.payload["action"] == "respond"
    assert outcome.payload["response_kind"] == "ask_user"
    assert outcome.payload["queued_in_turn"] is True
    assert outcome.payload["turn_id"] == live_turn_id
    assert outcome.payload["queue_id"] is not None
    assert len(store.get(cid, "u1")["turns"]) == 1
