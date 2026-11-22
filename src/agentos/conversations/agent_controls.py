"""Controls (stop and direct user/parent messages) addressed to a specific subagent in a conversation.

``AgentControlStore`` persists controls in ``conversation_agent_controls`` so an API process can reach a worker
running the turn in another process; ``InMemoryAgentControls`` provides the same interface when no database engine
is attached.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Mapping
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from agentos.api.contracts import ApplicationValidationError
from agentos.persistence.postgres.schema import conversation_agent_controls

MAX_CONTROL_MESSAGE_CHARS = 8_000
MAX_PREVIEW_CHARS = 200
MAX_TAKEN = 50
VALID_SENDER_KINDS = frozenset({"user", "parent"})
VALID_OUTCOMES = frozenset({"injected", "delivered", "forwarded", "expired", "stopped"})


def _validate_message(content: object) -> str:
    cleaned = str(content or "").strip()
    if not cleaned or len(cleaned) > MAX_CONTROL_MESSAGE_CHARS:
        raise ApplicationValidationError(
            f"message must be between 1 and {MAX_CONTROL_MESSAGE_CHARS} characters"
        )
    return cleaned


def _validate_sender_kind(sender_kind: object) -> str:
    cleaned = str(sender_kind or "user").strip().lower()
    if cleaned not in VALID_SENDER_KINDS:
        raise ApplicationValidationError("sender_kind must be 'user' or 'parent'")
    return cleaned


def _clean_sender_agent_id(sender_agent_id: object) -> str | None:
    if sender_agent_id is None:
        return None
    cleaned = str(sender_agent_id).strip()
    return cleaned[:255] if cleaned else None


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _message_state(consumed_at: object, outcome: object) -> str:
    raw = str(outcome or "").strip().lower()
    if raw:
        return raw
    return "queued" if consumed_at is None else "injected"


def _format_listed_message(row: Mapping[str, object]) -> dict[str, object]:
    content = str(row.get("content") or "")
    created_at = row.get("created_at")
    delivered_at = row.get("delivered_at")
    return {
        "control_id": str(row["control_id"]),
        "agent_id": str(row["agent_id"]),
        "sender_kind": str(row.get("sender_kind") or "user"),
        "sender_agent_id": str(row["sender_agent_id"]) if row.get("sender_agent_id") else None,
        "message": content,
        "preview": content[:MAX_PREVIEW_CHARS],
        "state": _message_state(row.get("consumed_at"), row.get("outcome")),
        "created_at": _ensure_utc(created_at) if isinstance(created_at, datetime) else created_at,
        "delivered_at": _ensure_utc(delivered_at) if isinstance(delivered_at, datetime) else delivered_at,
    }


def _format_taken_control(row: Mapping[str, object], *, outcome_override: str | None = None) -> dict[str, object]:
    return {
        "control_id": str(row["control_id"]),
        "kind": str(row["kind"]),
        "content": str(row["content"]),
        "outcome": outcome_override if outcome_override is not None else row.get("outcome"),
        "sender_kind": str(row.get("sender_kind") or "user"),
        "sender_agent_id": str(row["sender_agent_id"]) if row.get("sender_agent_id") else None,
    }


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
                    sender_kind="user",
                    sender_agent_id=None,
                    delivered_at=None,
                )
            )
        return control_id

    def post_message(
        self,
        user_id: str,
        conversation_id: str,
        agent_id: str,
        content: str,
        *,
        sender_kind: str = "user",
        sender_agent_id: str | None = None,
    ) -> str:
        """Enqueue a direct user or parent message (1..8000 stripped chars) for ``agent_id``."""
        cleaned = _validate_message(content)
        clean_sender_kind = _validate_sender_kind(sender_kind)
        clean_sender_agent = _clean_sender_agent_id(sender_agent_id)
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
                    sender_kind=clean_sender_kind,
                    sender_agent_id=clean_sender_agent,
                    delivered_at=None,
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
        """Take pending controls in order, marking ``consumed_at`` (and optional ``outcome``).

        Each row is claimed with ``consumed_at IS NULL`` in the UPDATE itself, so two concurrent takers never both
        return the same control.
        """
        now = datetime.now(UTC)
        claimed = []
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
            values: dict[str, object] = {"consumed_at": now}
            if outcome is not None:
                values["outcome"] = outcome
                if outcome == "delivered":
                    values["delivered_at"] = now
            for row in rows:
                result = connection.execute(
                    update(conversation_agent_controls)
                    .where(
                        conversation_agent_controls.c.id == row["id"],
                        conversation_agent_controls.c.consumed_at.is_(None),
                    )
                    .values(**values)
                )
                if result.rowcount == 1:
                    claimed.append(row)
        return [_format_taken_control(row, outcome_override=outcome) for row in claimed]

    def withdraw(self, control_id: str) -> bool:
        """Expire ``control_id`` if nobody has taken it yet; False when a worker already consumed it."""
        with self._engine.begin() as connection:
            result = connection.execute(
                update(conversation_agent_controls)
                .where(
                    conversation_agent_controls.c.control_id == str(control_id),
                    conversation_agent_controls.c.consumed_at.is_(None),
                )
                .values(consumed_at=datetime.now(UTC), outcome="expired")
            )
        return result.rowcount == 1

    def set_outcome(self, control_id: str, outcome: str) -> None:
        """Record the outcome ('injected' | 'delivered' | 'stopped' | 'expired' | 'forwarded') for a control."""
        clean_outcome = str(outcome)
        cid = str(control_id)
        if clean_outcome == "delivered":
            self.mark_delivered(cid)
            return
        stmt = update(conversation_agent_controls).where(
            conversation_agent_controls.c.control_id == cid
        )
        if clean_outcome == "injected":
            stmt = stmt.where(conversation_agent_controls.c.outcome.is_(None))
        with self._engine.begin() as connection:
            connection.execute(stmt.values(outcome=clean_outcome))

    def mark_delivered(self, control_id: str, *, delivered_at: datetime | None = None) -> bool:
        """Idempotently transition a control to ``delivered`` and stamp UTC ``delivered_at``.

        Returns True only on the first transition so callers never emit duplicate delivery events.
        """
        cid = str(control_id)
        stamp = _ensure_utc(delivered_at) if delivered_at is not None else datetime.now(UTC)
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    select(
                        conversation_agent_controls.c.id,
                        conversation_agent_controls.c.outcome,
                        conversation_agent_controls.c.delivered_at,
                    ).where(conversation_agent_controls.c.control_id == cid)
                )
                .mappings()
                .first()
            )
            if row is None or str(row["outcome"] or "") == "delivered":
                return False
            result = connection.execute(
                update(conversation_agent_controls)
                .where(
                    conversation_agent_controls.c.id == row["id"],
                    (
                        conversation_agent_controls.c.outcome.is_(None)
                        | (conversation_agent_controls.c.outcome != "delivered")
                    ),
                )
                .values(
                    outcome="delivered",
                    delivered_at=row["delivered_at"] if row["delivered_at"] is not None else stamp,
                )
            )
        return result.rowcount == 1

    def get(self, control_id: str) -> dict[str, object] | None:
        """Return one message control formatted like ``list_for_agent``, or None when absent."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(conversation_agent_controls).where(
                        conversation_agent_controls.c.control_id == str(control_id)
                    )
                )
                .mappings()
                .first()
            )
        return _format_listed_message(row) if row is not None else None

    def list_for_agent(
        self,
        user_id: str,
        conversation_id: str,
        agent_id: str,
    ) -> list[dict[str, object]]:
        """List all message controls addressed to ``agent_id`` in arrival order."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(conversation_agent_controls)
                    .where(
                        conversation_agent_controls.c.user_id == user_id,
                        conversation_agent_controls.c.conversation_id == conversation_id,
                        conversation_agent_controls.c.agent_id == agent_id,
                        conversation_agent_controls.c.kind == "message",
                    )
                    .order_by(conversation_agent_controls.c.id)
                )
                .mappings()
                .all()
            )
        return [_format_listed_message(row) for row in rows]

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
                    "sender_kind": "user",
                    "sender_agent_id": None,
                    "delivered_at": None,
                }
            )
            return control_id

    def post_message(
        self,
        user_id: str,
        conversation_id: str,
        agent_id: str,
        content: str,
        *,
        sender_kind: str = "user",
        sender_agent_id: str | None = None,
    ) -> str:
        cleaned = _validate_message(content)
        clean_sender_kind = _validate_sender_kind(sender_kind)
        clean_sender_agent = _clean_sender_agent_id(sender_agent_id)
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
                    "sender_kind": clean_sender_kind,
                    "sender_agent_id": clean_sender_agent,
                    "delivered_at": None,
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
                    if outcome == "delivered" and row["delivered_at"] is None:
                        row["delivered_at"] = now
            return [_format_taken_control(row) for row in pending]

    def set_outcome(self, control_id: str, outcome: str) -> None:
        clean_outcome = str(outcome)
        if clean_outcome == "delivered":
            self.mark_delivered(str(control_id))
            return
        with self._lock:
            for row in self._rows:
                if row["control_id"] == str(control_id):
                    if clean_outcome == "injected" and row.get("outcome") is not None:
                        continue
                    row["outcome"] = clean_outcome

    def mark_delivered(self, control_id: str, *, delivered_at: datetime | None = None) -> bool:
        cid = str(control_id)
        stamp = _ensure_utc(delivered_at) if delivered_at is not None else datetime.now(UTC)
        with self._lock:
            for row in self._rows:
                if row["control_id"] == cid:
                    if str(row.get("outcome") or "") == "delivered":
                        return False
                    row["outcome"] = "delivered"
                    if row["delivered_at"] is None:
                        row["delivered_at"] = stamp
                    return True
        return False

    def get(self, control_id: str) -> dict[str, object] | None:
        cid = str(control_id)
        with self._lock:
            for row in self._rows:
                if row["control_id"] == cid:
                    return _format_listed_message(row)
        return None

    def list_for_agent(
        self,
        user_id: str,
        conversation_id: str,
        agent_id: str,
    ) -> list[dict[str, object]]:
        with self._lock:
            matching = [
                row
                for row in self._rows
                if row["user_id"] == user_id
                and row["conversation_id"] == conversation_id
                and row["agent_id"] == agent_id
                and row["kind"] == "message"
            ]
            return [_format_listed_message(row) for row in matching]

    def withdraw(self, control_id: str) -> bool:
        with self._lock:
            for row in self._rows:
                if row["control_id"] == str(control_id) and row["consumed_at"] is None:
                    row["consumed_at"] = datetime.now(UTC)
                    row["outcome"] = "expired"
                    return True
        return False

    def expire_pending(self, user_id: str, conversation_id: str, agent_id: str) -> list[dict[str, object]]:
        return self.take(user_id, conversation_id, agent_id, outcome="expired")


__all__ = [
    "AgentControlStore",
    "InMemoryAgentControls",
    "MAX_CONTROL_MESSAGE_CHARS",
    "MAX_PREVIEW_CHARS",
    "VALID_OUTCOMES",
    "VALID_SENDER_KINDS",
]
