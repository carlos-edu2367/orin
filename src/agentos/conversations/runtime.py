"""Conversation turn lifecycle shared by durable and in-memory adapters.

The in-memory implementation is intentionally small but exercises the same
observable queue contract as the durable runtime: a turn is first durable and
queued, is explicitly acquired, and is either terminal or made retryable by
the watchdog.  It is useful for fast unit tests; it is never composed by ASGI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterable
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class ProviderDelta:
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise ValueError("provider delta must be text")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    message_id: str
    role: str
    content: str
    status: str
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class ChatTurn:
    turn_id: str
    state: str


@dataclass(frozen=True, slots=True)
class ChatConversation:
    conversation_id: str
    messages: tuple[ChatMessage, ...]
    turns: tuple[ChatTurn, ...]


@dataclass(frozen=True, slots=True)
class ChatReceipt:
    conversation_id: str
    turn_id: str


@dataclass(slots=True)
class _StoredTurn:
    turn_id: str
    conversation_id: str
    user_id: str
    provider: str
    model_id: str
    created_at: datetime
    state: str = "queued"
    dispatched: bool = False
    assistant_content: str = ""


class InMemoryChatRuntime:
    """Deterministic contract adapter; production uses ``PostgresChatRuntime``."""

    def __init__(self, *, now: Callable[[], datetime]) -> None:
        self._now = now
        self._turns: dict[str, _StoredTurn] = {}
        self._conversations: dict[str, list[ChatMessage]] = {}
        self._conversation_turns: dict[str, list[str]] = {}
        self._idempotency: dict[tuple[str, str], ChatReceipt] = {}

    def create(self, *, user_id: str, message: str, provider: str, model_id: str, idempotency_key: str) -> ChatReceipt:
        if not message.strip() or not provider.strip() or not model_id.strip() or not idempotency_key.strip():
            raise ValueError("conversation fields must be non-blank")
        key = (user_id, idempotency_key)
        if key in self._idempotency:
            return self._idempotency[key]
        conversation_id = f"chat_{uuid4().hex}"
        turn_id = f"turn_{uuid4().hex}"
        turn = _StoredTurn(turn_id, conversation_id, user_id, provider, model_id, self._now())
        self._turns[turn_id] = turn
        self._conversations[conversation_id] = [
            ChatMessage(f"msg_{uuid4().hex}", "user", message.strip(), "completed"),
            ChatMessage(f"msg_{uuid4().hex}", "assistant", "", "streaming"),
        ]
        self._conversation_turns[conversation_id] = [turn_id]
        receipt = ChatReceipt(conversation_id, turn_id)
        self._idempotency[key] = receipt
        return receipt

    def publish_pending(self) -> tuple[str, ...]:
        published = []
        for turn in self._turns.values():
            if turn.state == "queued" and not turn.dispatched:
                turn.dispatched = True
                published.append(turn.turn_id)
        return tuple(published)

    def run_once(self, turn_id: str, *, provider_stream: Iterable[ProviderDelta]) -> None:
        turn = self._turns[turn_id]
        if not turn.dispatched or turn.state != "queued":
            raise ValueError("turn is not dispatchable")
        turn.state = "starting"
        turn.state = "running"
        content = "".join(delta.content for delta in provider_stream)
        turn.assistant_content = content
        turn.state = "completed"
        messages = self._conversations[turn.conversation_id]
        last = messages[-1]
        messages[-1] = ChatMessage(last.message_id, "assistant", content, "completed")

    def watchdog(self, observed_at: datetime, *, acquire_timeout: timedelta) -> tuple[str, ...]:
        expired: list[str] = []
        for turn in self._turns.values():
            if turn.state == "queued" and observed_at - turn.created_at >= acquire_timeout:
                turn.state = "failed"
                messages = self._conversations[turn.conversation_id]
                last = messages[-1]
                messages[-1] = ChatMessage(last.message_id, "assistant", "Não foi possível iniciar esta resposta.", "failed", True)
                expired.append(turn.turn_id)
        return tuple(expired)

    def get(self, conversation_id: str, user_id: str) -> ChatConversation:
        turns = self._conversation_turns.get(conversation_id)
        if not turns or self._turns[turns[0]].user_id != user_id:
            raise LookupError("conversation not found")
        return ChatConversation(
            conversation_id,
            tuple(self._conversations[conversation_id]),
            tuple(ChatTurn(turn_id, self._turns[turn_id].state) for turn_id in turns),
        )


__all__ = ["ChatConversation", "ChatMessage", "ChatReceipt", "ChatTurn", "InMemoryChatRuntime", "ProviderDelta"]
