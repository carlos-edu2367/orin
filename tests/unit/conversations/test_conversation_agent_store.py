from __future__ import annotations

from sqlalchemy import create_engine

from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata


def _store() -> ConversationAgentStore:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return ConversationAgentStore(engine, conversation_id="chat-1", user_id="user-1")


def test_subagent_keeps_the_provider_and_model_it_was_created_with() -> None:
    store = _store()

    created = store.create(
        "Researcher",
        "Research the source material.",
        parent_agent_id="agent:chat-1:main",
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
    )

    assert created["provider"] == "openrouter"
    assert created["model_id"] == "anthropic/claude-sonnet-4"
    assert store.find("Researcher")["model_id"] == "anthropic/claude-sonnet-4"


def test_usage_is_aggregated_per_agent_without_treating_missing_usage_as_zero() -> None:
    store = _store()

    store.record_usage(
        "agent:chat-1:main",
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
        input_tokens=11,
        output_tokens=7,
        total_tokens=18,
    )
    store.record_usage(
        "agent:chat-1:researcher",
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )

    usage = store.usage_by_agent()

    assert usage["agent:chat-1:main"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "usage_reported": True,
    }
    assert usage["agent:chat-1:researcher"] == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "usage_reported": False,
    }
