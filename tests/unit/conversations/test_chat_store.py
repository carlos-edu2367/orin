from __future__ import annotations

from sqlalchemy import create_engine

from agentos.agentic.events import AgentActivityEventType
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata


def test_overview_returns_model_and_usage_for_each_agent_and_the_whole_conversation() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(
        user_id="user-1",
        message="Research the source material.",
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
        idempotency_key="request-1",
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    main_agent_id = store.main_agent_id(turn)
    agents = ConversationAgentStore(engine, conversation_id=receipt.conversation_id, user_id="user-1")
    child = agents.create(
        "Researcher",
        "Research the source material.",
        parent_agent_id=main_agent_id,
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
    )
    agents.record_usage(
        main_agent_id,
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
        input_tokens=13,
        output_tokens=5,
        total_tokens=18,
    )
    agents.record_usage(
        child["agent_id"],
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
        input_tokens=11,
        output_tokens=7,
        total_tokens=18,
    )

    overview = store.overview(receipt.conversation_id, "user-1")
    researcher = next(agent for agent in overview["agents"] if agent["agent_id"] == child["agent_id"])

    assert overview["token_usage"] == {
        "input_tokens": 24,
        "output_tokens": 12,
        "total_tokens": 36,
        "usage_reported": True,
    }
    assert researcher["provider"] == "openrouter"
    assert researcher["model_id"] == "anthropic/claude-sonnet-4"
    assert researcher["token_usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "usage_reported": True,
    }


def test_overview_marks_usage_as_unavailable_when_the_provider_never_reported_it() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(
        user_id="user-1",
        message="Research the source material.",
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
        idempotency_key="request-1",
    )

    overview = store.overview(receipt.conversation_id, "user-1")

    assert overview["token_usage"] == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "usage_reported": False,
    }


def test_reopened_conversation_keeps_text_deltas_interleaved_with_activity() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "test-cursor-secret"))
    receipt = store.create(
        user_id="user-1",
        message="Check the file.",
        provider="openrouter",
        model_id="model-a",
        idempotency_key="request-1",
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None

    store.delta(turn, "Vou verificar o arquivo. ")
    store.record(
        turn,
        AgentActivityEventType.FILESYSTEM_READ,
        "Leu o arquivo config.txt",
        {"path": "config.txt", "tool_name": "read_file", "tool_kind": "filesystem"},
    )
    store.delta(turn, "Entendi; vou aplicar a correção.")

    reopened = store.get(receipt.conversation_id, "user-1")
    timeline = [
        (item["event_type"], item["payload"].get("content") or item["summary"])
        for item in reopened["activities"]
        if item["event_type"] in {"assistant.delta", "filesystem.read"}
    ]

    assert timeline == [
        ("assistant.delta", "Vou verificar o arquivo. "),
        ("filesystem.read", "Leu o arquivo config.txt"),
        ("assistant.delta", "Entendi; vou aplicar a correção."),
    ]
