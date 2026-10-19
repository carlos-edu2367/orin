"""The mailbox of an agent team (W5): messages between the lead and its teammates, kept per conversation.

``AgentMailbox`` stores them in the database (``agent_mailbox``), so a conversation keeps the record of what its
team said; ``InMemoryMailbox`` is the same interface for a session without a database (tests, embedded use).
A message is ``pending`` until the recipient's next run takes it (``take``).
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import agent_mailbox

MAX_MESSAGE_CHARS = 16_000
MAX_SUMMARY_CHARS = 200
MAX_TAKEN = 50


def _clean(text: object, maximum: int) -> str:
    return str(text or "").strip()[:maximum]


class AgentMailbox:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        agent_mailbox.create(engine, checkfirst=True)

    def post(self, *, user_id: str, conversation_id: str, team_name: str, sender: str, recipient: str, content: str, summary: str = "") -> str:
        message_id = f"mail_{uuid4().hex[:16]}"
        with self._engine.begin() as connection:
            connection.execute(insert(agent_mailbox).values(
                message_id=message_id, conversation_id=conversation_id, user_id=user_id, team_name=_clean(team_name, 64),
                sender=_clean(sender, 64), recipient=_clean(recipient, 64), content=_clean(content, MAX_MESSAGE_CHARS),
                summary=_clean(summary, MAX_SUMMARY_CHARS), created_at=datetime.now(UTC), delivered_at=None,
            ))
        return message_id

    def take(self, *, user_id: str, conversation_id: str, recipient: str) -> list[dict[str, str]]:
        """The recipient's pending messages, oldest first, marked delivered (each is taken once)."""
        with self._engine.begin() as connection:
            rows = connection.execute(select(agent_mailbox).where(
                agent_mailbox.c.user_id == user_id, agent_mailbox.c.conversation_id == conversation_id,
                agent_mailbox.c.recipient == recipient, agent_mailbox.c.delivered_at.is_(None),
            ).order_by(agent_mailbox.c.id).limit(MAX_TAKEN)).mappings().all()
            if rows:
                connection.execute(update(agent_mailbox).where(agent_mailbox.c.id.in_([row["id"] for row in rows])).values(delivered_at=datetime.now(UTC)))
        return [{"sender": str(row["sender"]), "content": str(row["content"]), "summary": str(row["summary"] or "")} for row in rows]

    def history(self, *, user_id: str, conversation_id: str, limit: int = 200) -> list[dict[str, str]]:
        with self._engine.connect() as connection:
            rows = connection.execute(select(agent_mailbox).where(
                agent_mailbox.c.user_id == user_id, agent_mailbox.c.conversation_id == conversation_id,
            ).order_by(agent_mailbox.c.id).limit(max(1, min(int(limit), 1000)))).mappings().all()
        return [{
            "sender": str(row["sender"]), "recipient": str(row["recipient"]), "team_name": str(row["team_name"]),
            "content": str(row["content"]), "summary": str(row["summary"] or ""), "delivered": row["delivered_at"] is not None,
        } for row in rows]


class InMemoryMailbox:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[dict[str, object]] = []

    def post(self, *, user_id: str, conversation_id: str, team_name: str, sender: str, recipient: str, content: str, summary: str = "") -> str:
        message_id = f"mail_{uuid4().hex[:16]}"
        with self._lock:
            self._rows.append({
                "message_id": message_id, "user_id": user_id, "conversation_id": conversation_id, "team_name": _clean(team_name, 64),
                "sender": _clean(sender, 64), "recipient": _clean(recipient, 64), "content": _clean(content, MAX_MESSAGE_CHARS),
                "summary": _clean(summary, MAX_SUMMARY_CHARS), "delivered": False,
            })
        return message_id

    def take(self, *, user_id: str, conversation_id: str, recipient: str) -> list[dict[str, str]]:
        with self._lock:
            pending = [row for row in self._rows if not row["delivered"] and row["user_id"] == user_id
                       and row["conversation_id"] == conversation_id and row["recipient"] == recipient][:MAX_TAKEN]
            for row in pending:
                row["delivered"] = True
        return [{"sender": str(row["sender"]), "content": str(row["content"]), "summary": str(row["summary"])} for row in pending]

    def history(self, *, user_id: str, conversation_id: str, limit: int = 200) -> list[dict[str, str]]:
        with self._lock:
            rows = [dict(row) for row in self._rows if row["user_id"] == user_id and row["conversation_id"] == conversation_id]
        return rows[: max(1, min(int(limit), 1000))]


__all__ = ["AgentMailbox", "InMemoryMailbox", "MAX_MESSAGE_CHARS"]
