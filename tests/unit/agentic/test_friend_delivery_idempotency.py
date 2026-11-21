"""WS-B8: Idempotency of Friend send_message calls with repeated tool_call_id (Critério 16)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import conversation_input_queue, conversation_turns, metadata
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


def _make_toolset(tmp_path: Path, store: PostgresChatStore) -> AgentToolset:
    toolset = AgentToolset(
        ConversationWorkspace.at_root(tmp_path),
        chat_store=store,
        project_user_id="u1",
        conversation_id="conv-friend-idem",
        default_provider="anthropic",
        default_model_id="claude-sonnet-4-5",
    )
    toolset._effective_settings = empty_settings()
    return toolset


def test_repeated_tool_call_id_on_running_turn_does_not_duplicate_queue_row(tmp_path: Path) -> None:
    """Critério 16 (fila): repetir o mesmo tool_call_id retorna a mesma linha sem duplicar."""
    engine = _make_engine()
    store = PostgresChatStore(engine)
    seed = store.create(
        user_id="u1",
        message="Inicie a tarefa",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="seed-idem-1",
        code_mode="code",
        permission_mode="accept_edits",
    )
    assert store.claim(seed.turn_id) is not None
    toolset = _make_toolset(tmp_path, store)

    first = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": seed.conversation_id,
            "message": "Não esqueça o log estruturado.",
        },
        call_id="toolu_repeat_001",
    )
    second = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": seed.conversation_id,
            "message": "Não esqueça o log estruturado.",
        },
        call_id="toolu_repeat_001",
    )

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert first.payload["delivery"] == "pending_in_turn"
    assert second.payload["delivery"] == "pending_in_turn"
    assert first.payload["queue_id"] == second.payload["queue_id"]
    assert first.payload["idempotent"] is True
    assert second.payload["idempotent"] is True

    with engine.connect() as conn:
        q_count = conn.execute(
            select(func.count())
            .select_from(conversation_input_queue)
            .where(conversation_input_queue.c.conversation_id == seed.conversation_id)
        ).scalar_one()
        t_count = conn.execute(
            select(func.count())
            .select_from(conversation_turns)
            .where(conversation_turns.c.conversation_id == seed.conversation_id)
        ).scalar_one()
    assert q_count == 1
    assert t_count == 1


def test_repeated_tool_call_id_on_idle_session_does_not_duplicate_turn_or_enqueue(tmp_path: Path) -> None:
    """Critério 16 (turno novo): repetir mesmo tool_call_id após criar turno novo não cria segundo turno nem enfileira na fila."""
    engine = _make_engine()
    store = PostgresChatStore(engine)
    seed = store.create(
        user_id="u1",
        message="Primeira tarefa",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="seed-idem-2",
        code_mode="code",
        permission_mode="accept_edits",
    )
    claimed = store.claim(seed.turn_id)
    assert claimed is not None
    store.finish(claimed)

    toolset = _make_toolset(tmp_path, store)

    first = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": seed.conversation_id,
            "message": "Comece a fase 2.",
        },
        call_id="toolu_repeat_002",
    )
    # Note: after `first`, the new turn is now in `queued` state (live!).
    # Repeating the exact same tool_call_id must NOT enqueue into that live turn nor create a 3rd turn!
    second = toolset.invoke(
        "manage_session",
        {
            "action": "send_message",
            "conversation_id": seed.conversation_id,
            "message": "Comece a fase 2.",
        },
        call_id="toolu_repeat_002",
    )

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert first.payload["delivery"] == "new_turn"
    assert second.payload["turn_id"] == first.payload["turn_id"]
    assert second.payload["queue_id"] is None
    assert second.payload["idempotent"] is True

    with engine.connect() as conn:
        q_count = conn.execute(
            select(func.count())
            .select_from(conversation_input_queue)
            .where(conversation_input_queue.c.conversation_id == seed.conversation_id)
        ).scalar_one()
        t_count = conn.execute(
            select(func.count())
            .select_from(conversation_turns)
            .where(conversation_turns.c.conversation_id == seed.conversation_id)
        ).scalar_one()
    assert q_count == 0
    assert t_count == 2


def test_direct_call_without_tool_call_id_marks_idempotent_false(tmp_path: Path) -> None:
    engine = _make_engine()
    store = PostgresChatStore(engine)
    seed = store.create(
        user_id="u1",
        message="Inicie a tarefa",
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        idempotency_key="seed-idem-3",
        code_mode="code",
        permission_mode="accept_edits",
    )
    assert store.claim(seed.turn_id) is not None
    toolset = _make_toolset(tmp_path, store)

    # Calling manage_session directly without invoke()/call_id has no active invocation id
    toolset._active_invocation_id = None
    outcome = toolset.manage_session(
        action="send_message",
        conversation_id=seed.conversation_id,
        message="Mensagem direta sem invocation id.",
    )
    assert outcome.status == "succeeded"
    assert outcome.payload["delivery"] == "pending_in_turn"
    assert outcome.payload["idempotent"] is False
