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

from agentos.agentic.events import AgentActivityEvent, AgentActivityEventType
from agentos.agentic.models import (
    MAX_ACTION_DEPTH,
    MAX_ACTION_ITEMS,
    MAX_ACTION_TEXT,
    MAX_APPROVAL_DEPTH,
    MAX_APPROVAL_ITEMS,
    MAX_APPROVAL_TEXT,
    MAX_CODE_PROPOSAL_TEXT,
    MAX_USER_QUESTION_DEPTH,
    MAX_USER_QUESTION_ITEMS,
    MAX_USER_QUESTION_TEXT,
    _LARGE_BUDGET_KEYS,
)

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


def _coerce_stored_value(
    value: object,
    *,
    depth: int = 0,
    count: list[int] | None = None,
    text_limit: int = MAX_ACTION_TEXT,
    item_limit: int = MAX_ACTION_ITEMS,
    depth_limit: int = MAX_ACTION_DEPTH,
) -> object:
    if count is None:
        count = [0]
    if depth > depth_limit:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:text_limit] if len(value) > text_limit else value
    if depth >= depth_limit:
        return str(value)[:text_limit]
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        valid_items = [
            (k, v)
            for k, v in value.items()
            if isinstance(k, str) and k.strip()
        ][:item_limit]
        for idx, (k, v) in enumerate(valid_items):
            remaining_siblings = (len(valid_items) - idx - 1) if depth == 0 else 0
            effective_item_limit = max(count[0], item_limit - remaining_siblings)
            if len(out) >= item_limit or count[0] >= effective_item_limit:
                break
            is_large_key = depth == 0 and k in _LARGE_BUDGET_KEYS and isinstance(v, (Mapping, tuple, list))
            if (
                not is_large_key
                and isinstance(v, (Mapping, list, tuple))
                and len(v) > 0
                and count[0] + 2 > effective_item_limit
            ):
                if depth == 0:
                    continue
                break
            count[0] += 1
            if is_large_key:
                is_questions = k == "questions"
                is_code_proposal = k == "code_session_proposal"
                out[k] = _coerce_stored_value(
                    v,
                    depth=depth + 1,
                    count=[0],
                    text_limit=(
                        MAX_CODE_PROPOSAL_TEXT
                        if is_code_proposal
                        else (MAX_USER_QUESTION_TEXT if is_questions else MAX_APPROVAL_TEXT)
                    ),
                    item_limit=MAX_USER_QUESTION_ITEMS if is_questions else MAX_APPROVAL_ITEMS,
                    depth_limit=MAX_USER_QUESTION_DEPTH if is_questions else MAX_APPROVAL_DEPTH,
                )
            else:
                sub_limit = MAX_APPROVAL_TEXT if depth == 0 and k in {"output_tail", "plan_content"} else text_limit
                out[k] = _coerce_stored_value(
                    v,
                    depth=depth + 1,
                    count=count,
                    text_limit=sub_limit,
                    item_limit=effective_item_limit,
                    depth_limit=depth_limit,
                )
        return out
    if isinstance(value, (list, tuple)):
        out_list: list[object] = []
        for item in value:
            if len(out_list) >= item_limit or count[0] >= item_limit:
                break
            if isinstance(item, (Mapping, list, tuple)) and len(item) > 0 and count[0] + 2 > item_limit:
                break
            count[0] += 1
            out_list.append(
                _coerce_stored_value(
                    item,
                    depth=depth + 1,
                    count=count,
                    text_limit=text_limit,
                    item_limit=item_limit,
                    depth_limit=depth_limit,
                )
            )
        return out_list
    return str(value)[:text_limit]


def _coerce_stored_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        return {}
    coerced = _coerce_stored_value(payload, depth=0, count=[0], text_limit=512, item_limit=64)
    return dict(coerced) if isinstance(coerced, Mapping) else {}


def _event_from_row(row: Mapping[str, object]) -> AgentActivityEvent:
    coerced_payload = _coerce_stored_payload(row["payload"])
    raw_visibility = str(row["visibility"] or "public").strip().lower()
    visibility = raw_visibility if raw_visibility in {"public", "owner", "internal"} else "public"
    raw_summary = str(row["summary"] or "").strip()
    summary = raw_summary[:512] if raw_summary else "Atividade"
    try:
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
            summary=summary,
            payload=coerced_payload,
            visibility=visibility,
            created_at=_aware(row["created_at"]),
        )
    except ValueError:
        raw_ev_type = str(row["event_type"] or "")
        safe_ev_type = (
            raw_ev_type
            if raw_ev_type in AgentActivityEventType._value2member_map_
            else AgentActivityEventType.TOOL_PROGRESSED.value
        )
        return AgentActivityEvent(
            event_id=str(row["event_id"] or "").strip() or "evt_recovered",
            conversation_id=str(row["conversation_id"] or "").strip() or "conv_recovered",
            turn_id=str(row["turn_id"] or "").strip() or "turn_recovered",
            execution_id=str(row["execution_id"] or "").strip() or "exec_recovered",
            user_id=str(row["user_id"] or "").strip() or "user_recovered",
            workspace_id=(
                row["workspace_id"]
                if isinstance(row["workspace_id"], str) and row["workspace_id"].strip()
                else None
            ),
            agent_id=str(row["agent_id"] or "").strip() or "agent:recovered",
            parent_agent_id=(
                row["parent_agent_id"]
                if isinstance(row["parent_agent_id"], str) and row["parent_agent_id"].strip()
                else None
            ),
            event_type=safe_ev_type,
            sequence=max(1, int(row["sequence"] or 1)),
            summary=summary,
            payload={},
            visibility=visibility,
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
