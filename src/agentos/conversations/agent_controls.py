"""Controls (stop and direct user messages) addressed to a specific subagent in a conversation.

``AgentControlStore`` persists controls in ``conversation_agent_controls`` so an API process can reach a worker
running the turn in another process; ``InMemoryAgentControls`` provides the same interface when no database engine
is attached.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from agentos.api.contracts import ApplicationValidationError
from agentos.persistence.postgres.schema import conversation_agent_controls

MAX_CONTROL_MESSAGE_CHARS = 8_000
MAX_TAKEN = 50


def _validate_message(content: object) -> str:
    cleaned = str(content or "").strip()
    if not cleaned or len(cleaned) > MAX_CONTROL_MESSAGE_CHARS:
        raise ApplicationValidationError(
            f"message must be between 1 and {MAX_CONTROL_MESSAGE_CHARS} characters"
        )
    return cleaned


class AgentControlStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        conversation_agent_controls.create(engine, checkfirst=True)

    def request_stop(self, user_id: str, conversation_id: str, agent_id: str) -> str:
        """Idempotently enqueue a stop control for ``agent_id``; returns existing pending control_id if present."""
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(conversation_agent_controls.c.control_id)
                .where(
                    conversation_agent_controls.c.user_id == user_id,
                    conversation_agent_controls.c.conversation_id == conversation_id,
                    conversation_agent_controls.c.agent_id == agent_id,
                    conversation_agent_controls.c.kind == "stop",
                    conversation_agent_controls.c.consumed_at.is_(None),
                )
                .order_by(conversation_agent_controls.c.id)
                .limit(1)
            ).scalar()
            if existing is not None:
                return str(existing)
            control_id = f"ctrl_{uuid4().hex[:16]}"
            connection.execute(
                insert(conversation_agent_controls).values(
                    control_id=control_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    kind="stop",
                    content="",
                    created_at=datetime.now(UTC),
                    consumed_at=None,
                    outcome=None,
                )
            )
        return control_id

    def post_message(self, user_id: str, conversation_id: str, agent_id: str, content: str) -> str:
        """Enqueue a direct user message (1..8000 stripped chars) for ``agent_id``."""
        cleaned = _validate_message(content)
        control_id = f"ctrl_{uuid4().hex[:16]}"
        with self._engine.begin() as connection:
            connection.execute(
                insert(conversation_agent_controls).values(
                    control_id=control_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    kind="message",
                    content=cleaned,
                    created_at=datetime.now(UTC),
                    consumed_at=None,
                    outcome=None,
                )
            )
        return control_id

    def take(
        self,
        user_id: str,
        conversation_id: str,
        agent_id: str,
        *,
        outcome: str | None = None,
    ) -> list[dict[str, object]]:
        """Take pending controls in order, marking ``consumed_at`` (and optional ``outcome``)."""
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            rows = (
                connection.execute(
                    select(conversation_agent_controls)
                    .where(
                        conversation_agent_controls.c.user_id == user_id,
                        conversation_agent_controls.c.conversation_id == conversation_id,
                        conversation_agent_controls.c.agent_id == agent_id,
                        conversation_agent_controls.c.consumed_at.is_(None),
                    )
                    .order_by(conversation_agent_controls.c.id)
                    .limit(MAX_TAKEN)
                )
                .mappings()
                .all()
            )
            if rows:
                values: dict[str, object] = {"consumed_at": now}
                if outcome is not None:
                    values["outcome"] = outcome
                connection.execute(
                    update(conversation_agent_controls)
                    .where(conversation_agent_controls.c.id.in_([row["id"] for row in rows]))
                    .values(**values)
                )
        return [
            {
                "control_id": str(row["control_id"]),
                "kind": str(row["kind"]),
                "content": str(row["content"]),
                "outcome": outcome if outcome is not None else row["outcome"],
            }
            for row in rows
        ]

    def set_outcome(self, control_id: str, outcome: str) -> None:
        """Record the final outcome ('delivered' | 'stopped' | 'expired' | 'forwarded') for a taken control."""
        with self._engine.begin() as connection:
            connection.execute(
                update(conversation_agent_controls)
                .where(conversation_agent_controls.c.control_id == str(control_id))
                .values(outcome=str(outcome))
            )

    def expire_pending(self, user_id: str, conversation_id: str, agent_id: str) -> list[dict[str, object]]:
        """Mark all currently pending controls for ``agent_id`` as consumed with ``outcome='expired'``."""
        return self.take(user_id, conversation_id, agent_id, outcome="expired")


class InMemoryAgentControls:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[dict[str, object]] = []

    def request_stop(self, user_id: str, conversation_id: str, agent_id: str) -> str:
        with self._lock:
            for row in self._rows:
                if (
                    row["consumed_at"] is None
                    and row["user_id"] == user_id
                    and row["conversation_id"] == conversation_id
                    and row["agent_id"] == agent_id
                    and row["kind"] == "stop"
                ):
                    return str(row["control_id"])
            control_id = f"ctrl_{uuid4().hex[:16]}"
            self._rows.append(
                {
                    "control_id": control_id,
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "agent_id": agent_id,
                    "kind": "stop",
                    "content": "",
                    "created_at": datetime.now(UTC),
                    "consumed_at": None,
                    "outcome": None,
                }
            )
            return control_id

    def post_message(self, user_id: str, conversation_id: str, agent_id: str, content: str) -> str:
        cleaned = _validate_message(content)
        control_id = f"ctrl_{uuid4().hex[:16]}"
        with self._lock:
            self._rows.append(
                {
                    "control_id": control_id,
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "agent_id": agent_id,
                    "kind": "message",
                    "content": cleaned,
                    "created_at": datetime.now(UTC),
                    "consumed_at": None,
                    "outcome": None,
                }
            )
        return control_id

    def take(
        self,
        user_id: str,
        conversation_id: str,
        agent_id: str,
        *,
        outcome: str | None = None,
    ) -> list[dict[str, object]]:
        now = datetime.now(UTC)
        with self._lock:
            pending = [
                row
                for row in self._rows
                if row["consumed_at"] is None
                and row["user_id"] == user_id
                and row["conversation_id"] == conversation_id
                and row["agent_id"] == agent_id
            ][:MAX_TAKEN]
            for row in pending:
                row["consumed_at"] = now
                if outcome is not None:
                    row["outcome"] = outcome
            return [
                {
                    "control_id": str(row["control_id"]),
                    "kind": str(row["kind"]),
                    "content": str(row["content"]),
                    "outcome": row["outcome"],
                }
                for row in pending
            ]

    def set_outcome(self, control_id: str, outcome: str) -> None:
        with self._lock:
            for row in self._rows:
                if row["control_id"] == str(control_id):
                    row["outcome"] = str(outcome)

    def expire_pending(self, user_id: str, conversation_id: str, agent_id: str) -> list[dict[str, object]]:
        return self.take(user_id, conversation_id, agent_id, outcome="expired")


__all__ = ["AgentControlStore", "InMemoryAgentControls", "MAX_CONTROL_MESSAGE_CHARS"]
