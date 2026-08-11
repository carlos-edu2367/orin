from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agentos.agentic.events import AgentActivityEvent

from .schema import conversation_activity_events


class ActivityCursorError(ValueError):
    code = "cursor_invalid"
    resync_required = True


@dataclass(frozen=True, slots=True)
class ActivityPage:
    events: tuple[AgentActivityEvent, ...]
    next_cursor: str | None
    resync_required: bool = False
    # One signed cursor per event, positionally aligned with ``events``. It is
    # produced by the same query that reads the page so a caller can label every
    # event without issuing one extra round trip per event.
    cursors: tuple[str, ...] = ()


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _matches(row: Mapping[str, object], event: AgentActivityEvent) -> bool:
    return all(
        row[field] == expected
        for field, expected in (
            ("event_id", event.event_id),
            ("conversation_id", event.conversation_id),
            ("user_id", event.user_id),
            ("workspace_id", event.workspace_id),
            ("turn_id", event.turn_id),
            ("execution_id", event.execution_id),
            ("agent_id", event.agent_id),
            ("parent_agent_id", event.parent_agent_id),
            ("event_type", event.event_type.value),
            ("sequence", event.sequence),
            ("summary", event.summary),
            ("visibility", event.visibility.value),
        )
    ) and _plain(row["payload"]) == _plain(event.payload) and _aware(row["created_at"]) == event.created_at


def _event_from_row(row: Mapping[str, object]) -> AgentActivityEvent:
    return AgentActivityEvent(
        event_id=str(row["event_id"]),
        conversation_id=str(row["conversation_id"]),
        turn_id=str(row["turn_id"]),
        execution_id=str(row["execution_id"]),
        user_id=str(row["user_id"]),
        workspace_id=row["workspace_id"],
        agent_id=str(row["agent_id"]),
        parent_agent_id=row["parent_agent_id"],
        event_type=str(row["event_type"]),
        sequence=int(row["sequence"]),
        summary=str(row["summary"]),
        payload=row["payload"],
        visibility=str(row["visibility"]),
        created_at=_aware(row["created_at"]),
    )


class PostgresAgenticActivityStore:
    def __init__(self, engine: Engine, cursor_secret: str | bytes) -> None:
        self._engine = engine
        self._cursor_secret = cursor_secret.encode() if isinstance(cursor_secret, str) else cursor_secret
        if not self._cursor_secret:
            raise ValueError("cursor_secret must be non-empty")

    def record_event(self, event: AgentActivityEvent) -> bool:
        values = {
            "event_id": event.event_id,
            "conversation_id": event.conversation_id,
            "user_id": event.user_id,
            "workspace_id": event.workspace_id,
            "turn_id": event.turn_id,
            "execution_id": event.execution_id,
            "agent_id": event.agent_id,
            "parent_agent_id": event.parent_agent_id,
            "event_type": event.event_type.value,
            "sequence": event.sequence,
            "summary": event.summary,
            "payload": _plain(event.payload),
            "visibility": event.visibility.value,
            "created_at": event.created_at,
        }
        with self._engine.begin() as connection:
            existing = self._existing_by_event_id(connection, event.event_id)
            if existing is not None:
                if not _matches(existing, event):
                    raise ValueError("event_id already exists with a different event")
                return False
            try:
                with connection.begin_nested():
                    connection.execute(insert(conversation_activity_events).values(**values))
            except IntegrityError:
                existing = self._existing_by_event_id(connection, event.event_id)
                if existing is not None:
                    if not _matches(existing, event):
                        raise ValueError("event_id already exists with a different event")
                    return False
                raise ValueError("conversation turn sequence already exists") from None
            return True

    append = record_event
    insert = record_event
    insert_event = record_event

    def replay(
        self,
        user_id: str,
        conversation_id: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ActivityPage:
        if not user_id.strip() or not conversation_id.strip():
            raise ValueError("user_id and conversation_id must be non-blank")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        position = 0
        if cursor not in (None, "0"):
            position = self._decode_cursor(cursor, user_id, conversation_id)
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(conversation_activity_events)
                .where(
                    conversation_activity_events.c.user_id == user_id,
                    conversation_activity_events.c.conversation_id == conversation_id,
                    conversation_activity_events.c.id > position,
                )
                .order_by(conversation_activity_events.c.id)
                .limit(limit + 1)
            ).mappings().all()
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        next_cursor = None
        if visible_rows:
            next_cursor = self._encode_cursor(user_id, conversation_id, int(visible_rows[-1]["id"]))
        cursors = tuple(self._encode_cursor(user_id, conversation_id, int(row["id"])) for row in visible_rows)
        return ActivityPage(tuple(_event_from_row(row) for row in visible_rows), next_cursor, cursors=cursors)

    def cursor_for_event(self, user_id: str, conversation_id: str, event_id: str) -> str | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(conversation_activity_events.c.id).where(
                    conversation_activity_events.c.user_id == user_id,
                    conversation_activity_events.c.conversation_id == conversation_id,
                    conversation_activity_events.c.event_id == event_id,
                )
            ).scalar_one_or_none()
        return None if row is None else self._encode_cursor(user_id, conversation_id, int(row))

    list_events = replay
    replay_events = replay

    @staticmethod
    def _existing_by_event_id(connection, event_id: str):
        return connection.execute(
            select(conversation_activity_events).where(
                conversation_activity_events.c.event_id == event_id
            )
        ).mappings().first()

    def _encode_cursor(self, user_id: str, conversation_id: str, position: int) -> str:
        body = json.dumps(
            {"v": 1, "u": user_id, "c": conversation_id, "p": position},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        signature = hmac.new(self._cursor_secret, body, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(body + b"." + signature).decode().rstrip("=")
        return f"a.{encoded}"

    def _decode_cursor(self, cursor: str, user_id: str, conversation_id: str) -> int:
        try:
            prefix, encoded = cursor.split(".", 1)
            if prefix != "a":
                raise ValueError
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if len(raw) <= 33 or raw[-33] != ord("."):
                raise ValueError
            body, signature = raw[:-33], raw[-32:]
            expected = hmac.new(self._cursor_secret, body, hashlib.sha256).digest()
            payload = json.loads(body.decode())
            if not isinstance(payload, dict):
                raise ValueError
            if (
                not hmac.compare_digest(signature, expected)
                or payload != {"c": conversation_id, "p": payload.get("p"), "u": user_id, "v": 1}
                or not isinstance(payload["p"], int)
                or payload["p"] < 0
            ):
                raise ValueError
            return payload["p"]
        except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError, UnicodeDecodeError):
            raise ActivityCursorError("cursor_invalid; resync_required") from None


PostgresAgenticActivityAdapter = PostgresAgenticActivityStore
ActivityReplayPage = ActivityPage

__all__ = [
    "ActivityCursorError",
    "ActivityPage",
    "ActivityReplayPage",
    "PostgresAgenticActivityAdapter",
    "PostgresAgenticActivityStore",
]
