"""Durable chat behaviours that only PostgreSQL can prove.

These cover the paths the UI depends on most and that a fake store cannot
exercise honestly: reopening a finished conversation, cancelling a live turn,
and the aggregated overview.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from agentos.agentic.events import AgentActivityEventType
from agentos.api.events import CursorError
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.migrate import upgrade


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


@pytest.fixture()
def store() -> PostgresChatStore:
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    return PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "integration-cursor-secret"))


def _user() -> str:
    return f"user:{uuid4().hex}"


def test_a_finished_conversation_reopens_with_its_messages_and_activity(store: PostgresChatStore) -> None:
    user = _user()
    receipt = store.create(user_id=user, message="crie um arquivo", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    store.record(turn, AgentActivityEventType.TOOL_FINISHED, "Escreveu a.txt", {"tool_name": "write_file", "tool_kind": "filesystem", "status": "succeeded"})
    store.delta(turn, "pronto, criei o arquivo")
    store.finish(turn)

    snapshot = store.get(receipt.conversation_id, user)

    assert snapshot["state"] == "completed"
    assert [item["role"] for item in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["messages"][1]["content"] == "pronto, criei o arquivo"
    types = [item["event_type"] for item in snapshot["activities"]]
    assert "tool.finished" in types
    assert "turn.completed" in types
    # A reopened conversation must not replay thousands of one-token deltas.
    assert "assistant.delta" not in types


def test_the_live_stream_replays_deltas_that_the_snapshot_omits(store: PostgresChatStore) -> None:
    user = _user()
    receipt = store.create(user_id=user, message="escreva", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    store.delta(turn, "primeiro pedaço")

    events, cursor = store.events(receipt.conversation_id, user, "0")

    deltas = [item for item in events if item["event_type"] == "assistant.delta"]
    assert deltas and deltas[0]["payload"]["content"] == "primeiro pedaço"
    assert cursor != "0"

    # Reading again from the returned cursor yields nothing new.
    later, _ = store.events(receipt.conversation_id, user, cursor)
    assert later == []


def test_a_forged_cursor_is_rejected_as_a_resync_signal(store: PostgresChatStore) -> None:
    user = _user()
    receipt = store.create(user_id=user, message="oi", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)

    with pytest.raises(CursorError):
        store.events(receipt.conversation_id, user, "a.forged-cursor-value")


def test_cancelling_marks_a_running_turn_and_the_worker_can_observe_it(store: PostgresChatStore) -> None:
    user = _user()
    receipt = store.create(user_id=user, message="trabalhe muito", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)
    turn = store.claim(receipt.turn_id)
    assert turn is not None

    result = store.request_cancel(receipt.conversation_id, user)

    assert receipt.turn_id in result["cancelling"]
    assert store.cancel_requested(receipt.turn_id) is True

    store.finish(turn, failed=True, code="TURN_CANCELLED")
    snapshot = store.get(receipt.conversation_id, user)
    assert snapshot["state"] == "cancelled"
    # Cancelling is the user's choice, so it must not be offered as retryable.
    assert snapshot["messages"][1]["retryable"] is False


def test_cancelling_a_never_started_turn_finishes_it_immediately(store: PostgresChatStore) -> None:
    user = _user()
    receipt = store.create(user_id=user, message="nunca inicia", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)

    store.request_cancel(receipt.conversation_id, user)

    assert store.get(receipt.conversation_id, user)["state"] == "cancelled"


def test_a_terminal_turn_is_never_rewritten_by_a_late_finish(store: PostgresChatStore) -> None:
    user = _user()
    receipt = store.create(user_id=user, message="oi", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    store.finish(turn)

    store.finish(turn, failed=True, code="provider_unavailable")

    assert store.get(receipt.conversation_id, user)["state"] == "completed"


def test_the_overview_aggregates_agents_tools_and_messages(store: PostgresChatStore) -> None:
    user = _user()
    receipt = store.create(user_id=user, message="delegue", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    agents = ConversationAgentStore(store._engine, conversation_id=receipt.conversation_id, user_id=user)
    main = PostgresChatStore.main_agent_id(turn)
    child = agents.create("Researcher", "pesquisa", parent_agent_id=main)

    store.record(turn, AgentActivityEventType.AGENT_CREATED, "Criou o agente Researcher", {"agent_name": "Researcher"}, agent_id=child["agent_id"], parent_agent_id=main)
    store.record(turn, AgentActivityEventType.AGENT_MESSAGE_SENT, "Enviou uma tarefa", {"content": "investigue", "to_agent_id": child["agent_id"]}, agent_id=main)
    store.record(turn, AgentActivityEventType.AGENT_MESSAGE_RECEIVED, "Recebeu resposta", {"content": "achei", "from_agent_id": child["agent_id"]}, agent_id=child["agent_id"], parent_agent_id=main)
    store.record(turn, AgentActivityEventType.TOOL_FINISHED, "Consultou example.com", {"tool_name": "fetch_url", "tool_kind": "web", "status": "succeeded"}, agent_id=child["agent_id"])
    store.record(turn, AgentActivityEventType.TOOL_FINISHED, "run_command falhou", {"tool_name": "run_command", "tool_kind": "terminal", "status": "failed"}, agent_id=main)
    store.finish(turn)

    overview = store.overview(receipt.conversation_id, user)

    assert [item["name"] for item in overview["agents"]] == ["Main", "Researcher"]
    tools = {item["tool_name"]: item for item in overview["tools"]}
    assert tools["fetch_url"]["count"] == 1 and tools["fetch_url"]["failures"] == 0
    assert tools["run_command"]["failures"] == 1
    assert len(overview["messages"]) == 2
    assert overview["messages"][0]["from_agent_id"] == main
    assert overview["messages"][1]["to_agent_id"] == main
    assert overview["duration_seconds"] is not None


def test_activity_sequences_do_not_collide_under_rapid_writes(store: PostgresChatStore) -> None:
    user = _user()
    receipt = store.create(user_id=user, message="muitos eventos", provider="openrouter", model_id="model-a", idempotency_key=uuid4().hex)
    turn = store.claim(receipt.turn_id)
    assert turn is not None

    for index in range(25):
        store.record(turn, AgentActivityEventType.TOOL_FINISHED, f"Passo {index}", {"tool_name": "read_file", "tool_kind": "filesystem", "status": "succeeded"})

    events, _ = store.events(receipt.conversation_id, user, "0")
    summaries = [item["summary"] for item in events]
    assert all(f"Passo {index}" in summaries for index in range(25))


def test_memory_persists_for_the_user_across_conversations(store: PostgresChatStore) -> None:
    user = _user()
    memory = PostgresAgentMemoryStore(store._engine, user)
    memory.save("O usuário se chama Carlos.", ("perfil",))
    memory.save("Carlos prefere respostas curtas.", ("preferência",))

    # Both facts mention Carlos, so both are relevant; the one that also matches
    # "respostas" must rank first.
    ranked = [item["fact"] for item in memory.search("Carlos prefere respostas como?")]
    assert ranked[0] == "Carlos prefere respostas curtas."
    assert "O usuário se chama Carlos." in ranked
    assert memory.search("assunto totalmente diferente xyzzy") == []
    assert len(memory.recent()) == 2
    # Saving the same fact twice must not create a duplicate.
    memory.save("O usuário se chama Carlos.", ())
    assert len(memory.recent()) == 2
