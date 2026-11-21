"""WS-B8: Friend send_message delivery modes (now vs queue, fallbacks, cancelling, settings, control_code_session)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.prompts import FRIEND_SYSTEM_PROMPT_SECTION
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import MAX_QUEUED_INPUTS, PostgresChatStore
from agentos.persistence.postgres.schema import conversation_input_queue, conversation_turns, metadata
from agentos.settings.loader import EffectiveSettings, SettingsLayer, empty_settings


def _make_engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    return engine


def _make_friend_toolset(
    tmp_path: Path,
    store: PostgresChatStore,
    *,
    user_id: str = "u1",
    friend_cid: str = "conv-friend-1",
    settings: EffectiveSettings | None = None,
    dispatched: list[tuple[str, str]] | None = None,
) -> AgentToolset:
    toolset = AgentToolset(
        ConversationWorkspace.at_root(tmp_path),
        chat_store=store,
        project_user_id=user_id,
        conversation_id=friend_cid,
        default_provider="anthropic",
        default_model_id="claude-sonnet-4-5",
    )
    if settings is not None:
        toolset._effective_settings = settings
    else:
        toolset._effective_settings = empty_settings()
    if dispatched is not None:
        toolset._code_turn_dispatcher = lambda cid, tid: dispatched.append((cid, tid))
    return toolset


def _seed_target_session(
    store: PostgresChatStore,
    *,
    claim_turn: bool = True,
    code_mode: str = "code",
    provider: str = "anthropic",
    model_id: str = "claude-sonnet-4-5",
    permission_mode: str = "accept_edits",
) -> tuple[str, str, dict | None]:
    receipt = store.create(
        user_id="u1",
        message="Trabalhe na feature A",
        provider=provider,
        model_id=model_id,
        idempotency_key=f"seed-{code_mode}-{provider}-{model_id}",
        code_mode=code_mode,
        permission_mode=permission_mode,
    )
    claimed = None
    if claim_turn:
        claimed = store.claim(receipt.turn_id)
        assert claimed is not None
    return receipt.conversation_id, receipt.turn_id, claimed


def test_send_message_default_now_on_running_turn_queues_in_turn_without_new_turn(tmp_path: Path) -> None:
    """Critério 13 (parte 1): send_message sem delivery com turno running cria linha com origin='friend' e não cria turno novo."""
    engine = _make_engine()
    store = PostgresChatStore(engine)
    cid, live_turn_id, _claimed = _seed_target_session(store, claim_turn=True)
    dispatched: list[tuple[str, str]] = []
    toolset = _make_friend_toolset(tmp_path, store, dispatched=dispatched)

    outcome = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": cid,
            "message": "Inclua validação de email também.",
        },
        call_id="call-live-1",
    )
    assert outcome.status == "succeeded", outcome.content
    assert outcome.payload["delivery"] == "pending_in_turn"
    assert outcome.payload["queue_id"] is not None
    assert outcome.payload["turn_id"] == live_turn_id
    assert outcome.payload["delivery_fallback"] is None
    assert outcome.payload["idempotent"] is True
    assert dispatched == []

    # No new turn was created
    assert len(store.get(cid, "u1")["turns"]) == 1

    # Queue row has origin='friend' and origin_conversation_id=friend_cid
    with engine.connect() as conn:
        row = conn.execute(
            select(
                conversation_input_queue.c.queue_id,
                conversation_input_queue.c.origin,
                conversation_input_queue.c.origin_conversation_id,
                conversation_input_queue.c.state,
                conversation_input_queue.c.idempotency_key,
            ).where(conversation_input_queue.c.conversation_id == cid)
        ).mappings().one()
    assert row["queue_id"] == outcome.payload["queue_id"]
    assert row["origin"] == "friend"
    assert row["origin_conversation_id"] == "conv-friend-1"
    assert row["state"] == "pending"
    assert row["idempotency_key"] == "friend:conv-friend-1:call-live-1"


def test_send_message_without_live_turn_creates_new_turn(tmp_path: Path) -> None:
    engine = _make_engine()
    store = PostgresChatStore(engine)
    cid, first_turn_id, claimed = _seed_target_session(store, claim_turn=True)
    assert claimed is not None
    store.finish(claimed)
    dispatched: list[tuple[str, str]] = []
    toolset = _make_friend_toolset(tmp_path, store, dispatched=dispatched)

    outcome = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": cid,
            "message": "Agora rode os testes de regressão.",
        },
        call_id="call-new-1",
    )
    assert outcome.status == "succeeded"
    assert outcome.payload["delivery"] == "new_turn"
    assert outcome.payload["queue_id"] is None
    assert outcome.payload["turn_id"] != first_turn_id
    assert outcome.payload["delivery_fallback"] is None
    assert dispatched == [(cid, outcome.payload["turn_id"])]
    assert len(store.get(cid, "u1")["turns"]) == 2


def test_send_message_with_explicit_delivery_queue_creates_queued_next_turn(tmp_path: Path) -> None:
    """Critério 14 (parte 1): delivery='queue' cria turno queued como na 0.12.31."""
    engine = _make_engine()
    store = PostgresChatStore(engine)
    cid, live_turn_id, _claimed = _seed_target_session(store, claim_turn=True)
    dispatched: list[tuple[str, str]] = []
    toolset = _make_friend_toolset(tmp_path, store, dispatched=dispatched)

    outcome = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": cid,
            "message": "Quando terminar, revise o README.",
            "delivery": "queue",
        },
        call_id="call-queue-1",
    )
    assert outcome.status == "succeeded"
    assert outcome.payload["delivery"] == "queued_next_turn"
    assert outcome.payload["queue_id"] is None
    assert outcome.payload["turn_id"] != live_turn_id
    assert outcome.payload["state"] == "queued"
    assert outcome.payload["delivery_fallback"] is None
    assert store.queued_inputs(cid, "u1") == []
    assert len(store.get(cid, "u1")["turns"]) == 2
    assert dispatched == [(cid, outcome.payload["turn_id"])]


def test_send_message_with_provider_or_model_or_permission_change_falls_back_to_model_change(
    tmp_path: Path,
) -> None:
    """Critério 14 (parte 2): troca de provider, model_id ou permission_mode cai em queue com delivery_fallback='model_change'."""
    engine = _make_engine()
    store = PostgresChatStore(engine)
    cid, live_turn_id, _claimed = _seed_target_session(
        store,
        claim_turn=True,
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        permission_mode="accept_edits",
    )
    toolset = _make_friend_toolset(tmp_path, store)

    outcome = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": cid,
            "message": "Continue usando outro provedor.",
            "provider": "openai",
            "model_id": "gpt-5",
        },
        call_id="call-model-change-1",
    )
    assert outcome.status == "succeeded"
    assert outcome.payload["delivery"] == "queued_next_turn"
    assert outcome.payload["queue_id"] is None
    assert outcome.payload["turn_id"] != live_turn_id
    assert outcome.payload["delivery_fallback"] == "model_change"
    assert store.queued_inputs(cid, "u1") == []
    assert len(store.get(cid, "u1")["turns"]) == 2


def test_send_message_when_queue_is_full_falls_back_to_queue_full(tmp_path: Path) -> None:
    """Critério 14 (parte 3): fila cheia (MAX_QUEUED_INPUTS) cai em turno queued com delivery_fallback='queue_full'."""
    engine = _make_engine()
    store = PostgresChatStore(engine)
    cid, live_turn_id, _claimed = _seed_target_session(store, claim_turn=True)
    for idx in range(MAX_QUEUED_INPUTS):
        store.enqueue_input(user_id="u1", conversation_id=cid, content=f"item {idx}")

    toolset = _make_friend_toolset(tmp_path, store)
    outcome = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": cid,
            "message": "Mais uma instrução além do limite.",
        },
        call_id="call-qfull-1",
    )
    assert outcome.status == "succeeded"
    assert outcome.payload["delivery"] == "queued_next_turn"
    assert outcome.payload["queue_id"] is None
    assert outcome.payload["turn_id"] != live_turn_id
    assert outcome.payload["delivery_fallback"] == "queue_full"
    assert len(store.get(cid, "u1")["turns"]) == 2


def test_send_message_when_live_turn_is_cancelling_falls_back_to_turn_cancelling(tmp_path: Path) -> None:
    engine = _make_engine()
    store = PostgresChatStore(engine)
    cid, live_turn_id, _claimed = _seed_target_session(store, claim_turn=True)
    with engine.begin() as conn:
        conn.execute(
            conversation_turns.update()
            .where(conversation_turns.c.turn_id == live_turn_id)
            .values(state="cancelling")
        )

    toolset = _make_friend_toolset(tmp_path, store)
    outcome = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": cid,
            "message": "Tente novamente depois que cancelar.",
        },
        call_id="call-cancelling-1",
    )
    assert outcome.status == "succeeded"
    assert outcome.payload["delivery"] == "queued_next_turn"
    assert outcome.payload["queue_id"] is None
    assert outcome.payload["turn_id"] != live_turn_id
    assert outcome.payload["delivery_fallback"] == "turn_cancelling"
    assert store.queued_inputs(cid, "u1") == []


def test_friend_session_delivery_setting_queue_changes_default(tmp_path: Path) -> None:
    engine = _make_engine()
    store = PostgresChatStore(engine)
    cid, live_turn_id, _claimed = _seed_target_session(store, claim_turn=True)
    settings = EffectiveSettings(
        data={"friend": {"sessionDelivery": "queue"}},
        provenance={"friend.sessionDelivery": "user"},
        layers=(SettingsLayer("user", None, {"friend": {"sessionDelivery": "queue"}}),),
        errors=(),
        trusted=True,
    )
    toolset = _make_friend_toolset(tmp_path, store, settings=settings)

    outcome = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": cid,
            "message": "Respeite o padrão da configuração.",
        },
        call_id="call-setting-queue-1",
    )
    assert outcome.status == "succeeded"
    assert outcome.payload["delivery"] == "queued_next_turn"
    assert outcome.payload["queue_id"] is None
    assert outcome.payload["turn_id"] != live_turn_id
    assert store.queued_inputs(cid, "u1") == []


def test_control_code_session_send_message_supports_delivery_and_schema(tmp_path: Path) -> None:
    engine = _make_engine()
    store = PostgresChatStore(engine)
    cid, live_turn_id, _claimed = _seed_target_session(store, claim_turn=True, code_mode="code")
    toolset = _make_friend_toolset(tmp_path, store)

    # Verify schemas expose delivery on both manage_session and propose_code_session/control_code_session
    manage_props = toolset.resolve("manage_session").parameters["properties"]
    code_props = toolset.resolve("propose_code_session").parameters["properties"]
    assert manage_props["delivery"]["enum"] == ["now", "queue"]
    assert code_props["delivery"]["enum"] == ["now", "queue"]

    outcome = toolset.invoke(
        "control_code_session",
        {
            "action": "send_message",
            "code_conversation_id": cid,
            "message": "Adicione teste de borda no parser.",
        },
        call_id="call-code-live-1",
    )
    assert outcome.status == "succeeded"
    assert outcome.payload["delivery"] == "pending_in_turn"
    assert outcome.payload["queue_id"] is not None
    assert outcome.payload["turn_id"] == live_turn_id
    assert len(store.get(cid, "u1")["turns"]) == 1


def test_friend_prompt_teaches_delivery_queue_and_states() -> None:
    text = FRIEND_SYSTEM_PROMPT_SECTION.lower()
    assert 'delivery="queue"' in text or "delivery: \"queue\"" in text or "`delivery=\"queue\"`" in text
    assert "pending_in_turn" in text
    assert "queued_next_turn" in text
    assert "new_turn" in text
    assert "manda depois" in text
