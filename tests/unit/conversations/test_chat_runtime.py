from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


def test_queued_turn_is_dispatched_streamed_and_completed_without_exposing_provider_secret() -> None:
    """Break caught: a created conversation turn can remain QUEUED forever."""
    from agentos.conversations.runtime import InMemoryChatRuntime, ProviderDelta

    runtime = InMemoryChatRuntime(now=lambda: datetime(2026, 8, 10, tzinfo=UTC))
    receipt = runtime.create(user_id="user-a", message="O que você consegue fazer?", provider="openrouter", model_id="model-a", idempotency_key="message-1")

    runtime.publish_pending()
    runtime.run_once(receipt.turn_id, provider_stream=[ProviderDelta("Posso "), ProviderDelta("ajudar.")])

    conversation = runtime.get(receipt.conversation_id, "user-a")
    assert [message.content for message in conversation.messages] == ["O que você consegue fazer?", "Posso ajudar."]
    assert conversation.messages[-1].status == "completed"
    assert conversation.turns[-1].state == "completed"
    assert "api_key" not in repr(conversation)


def test_watchdog_makes_unacquired_turn_retryable_instead_of_leaving_it_queued() -> None:
    """Break caught: an unavailable worker leaves the user with a silent QUEUED turn."""
    from agentos.conversations.runtime import InMemoryChatRuntime

    started = datetime(2026, 8, 10, tzinfo=UTC)
    runtime = InMemoryChatRuntime(now=lambda: started)
    receipt = runtime.create(user_id="user-a", message="Verifique a fila", provider="openrouter", model_id="model-a", idempotency_key="message-2")

    expired = runtime.watchdog(started + timedelta(seconds=31), acquire_timeout=timedelta(seconds=30))

    assert expired == (receipt.turn_id,)
    conversation = runtime.get(receipt.conversation_id, "user-a")
    assert conversation.turns[-1].state == "failed"
    assert conversation.messages[-1].status == "failed"
    assert conversation.messages[-1].retryable is True
