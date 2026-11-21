"""Durable SQL store for the shared per-conversation task board (WS-B0, Spec 6.3)."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, and_, asc, bindparam, insert, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError

from .schema import task_board_items

VALID_TASK_STATUSES: frozenset[str] = frozenset(
    {"pending", "in_progress", "blocked", "done", "cancelled"}
)
_SQLITE_LOCK_RETRIES = 8

_CLAIM_SQLITE_SQL = text(
    """
    UPDATE task_board_items
    SET status = 'in_progress',
        owner_agent_id = :agent_id,
        lease_expires_at = :lease_expires_at,
        updated_at = :now
    WHERE item_id = :item_id
      AND conversation_id = :conversation_id
      AND status = 'pending'
      AND owner_agent_id IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM json_each(task_board_items.blocked_by) AS dep
          LEFT JOIN task_board_items AS blocker
            ON blocker.item_id = dep.value
           AND blocker.conversation_id = task_board_items.conversation_id
          WHERE blocker.item_id IS NULL OR blocker.status != 'done'
      )
    """
).bindparams(
    bindparam("lease_expires_at", type_=DateTime(timezone=True)),
    bindparam("now", type_=DateTime(timezone=True)),
)

_CLAIM_POSTGRES_SQL = text(
    """
    UPDATE task_board_items
    SET status = 'in_progress',
        owner_agent_id = :agent_id,
        lease_expires_at = :lease_expires_at,
        updated_at = :now
    WHERE item_id = :item_id
      AND conversation_id = :conversation_id
      AND status = 'pending'
      AND owner_agent_id IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM jsonb_array_elements_text(CAST(task_board_items.blocked_by AS jsonb)) AS dep(dep_id)
          LEFT JOIN task_board_items AS blocker
            ON blocker.item_id = dep.dep_id
           AND blocker.conversation_id = task_board_items.conversation_id
          WHERE blocker.item_id IS NULL OR blocker.status != 'done'
      )
    """
).bindparams(
    bindparam("lease_expires_at", type_=DateTime(timezone=True)),
    bindparam("now", type_=DateTime(timezone=True)),
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_status(status: str) -> str:
    cleaned = str(status or "").strip().lower()
    if cleaned not in VALID_TASK_STATUSES:
        allowed = ", ".join(sorted(VALID_TASK_STATUSES))
        raise ValueError(f"Invalid task status {status!r}; expected one of: {allowed}")
    return cleaned


def _clean_blocked_by(blocked_by: Sequence[str] | None) -> list[str]:
    if not blocked_by:
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in blocked_by:
        item = str(raw or "").strip()[:64]
        if item and item not in seen:
            seen.add(item)
            cleaned.append(item)
    return cleaned


def _is_sqlite_lock_error(exc: OperationalError) -> bool:
    msg = str(exc).lower()
    return "database is locked" in msg or "database table is locked" in msg or "busy" in msg


@dataclass(frozen=True, slots=True)
class TaskBoardItemRecord:
    item_id: str
    conversation_id: str
    team_name: str | None
    title: str
    description: str
    status: str
    owner_agent_id: str | None
    blocked_by: tuple[str, ...]
    lease_expires_at: datetime | None
    result_ref: str | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "conversation_id": self.conversation_id,
            "team_name": self.team_name,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "owner_agent_id": self.owner_agent_id,
            "blocked_by": list(self.blocked_by),
            "lease_expires_at": self.lease_expires_at,
            "result_ref": self.result_ref,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _row_to_record(row: Mapping[str, Any]) -> TaskBoardItemRecord:
    raw_blocked = row["blocked_by"]
    blocked: tuple[str, ...] = (
        tuple(str(item) for item in raw_blocked if str(item).strip())
        if isinstance(raw_blocked, (list, tuple))
        else ()
    )
    created = _as_utc(row["created_at"]) or datetime.now(UTC)
    updated = _as_utc(row["updated_at"]) or created
    return TaskBoardItemRecord(
        item_id=str(row["item_id"]),
        conversation_id=str(row["conversation_id"]),
        team_name=str(row["team_name"]) if row["team_name"] is not None else None,
        title=str(row["title"]),
        description=str(row["description"] or ""),
        status=str(row["status"]),
        owner_agent_id=str(row["owner_agent_id"]) if row["owner_agent_id"] is not None else None,
        blocked_by=blocked,
        lease_expires_at=_as_utc(row["lease_expires_at"]),
        result_ref=str(row["result_ref"]) if row["result_ref"] is not None else None,
        created_at=created,
        updated_at=updated,
    )


class TaskBoardStore:
    """Per-conversation task board with atomic SQL claim and lease expiration."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        return _as_utc(self._clock()) or datetime.now(UTC)

    def _run_write_tx(self, fn: Callable[[Connection], Any]) -> Any:
        if self._engine.dialect.name != "sqlite":
            with self._engine.begin() as conn:
                return fn(conn)

        last_err: OperationalError | None = None
        for attempt in range(_SQLITE_LOCK_RETRIES):
            try:
                with self._engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.exec_driver_sql("BEGIN IMMEDIATE")
                    try:
                        result = fn(conn)
                        conn.exec_driver_sql("COMMIT")
                        return result
                    except BaseException:
                        try:
                            conn.exec_driver_sql("ROLLBACK")
                        except Exception:
                            pass
                        raise
            except OperationalError as exc:
                if not _is_sqlite_lock_error(exc) or attempt + 1 >= _SQLITE_LOCK_RETRIES:
                    raise
                last_err = exc
                time.sleep(0.01 * (attempt + 1) + random.uniform(0.0, 0.015))
        if last_err is not None:  # pragma: no cover - defensive
            raise last_err
        raise RuntimeError("unreachable")  # pragma: no cover

    def create(
        self,
        *,
        conversation_id: str,
        title: str,
        description: str = "",
        item_id: str | None = None,
        team_name: str | None = None,
        status: str = "pending",
        owner_agent_id: str | None = None,
        blocked_by: Sequence[str] = (),
        lease_expires_at: datetime | None = None,
        result_ref: str | None = None,
    ) -> TaskBoardItemRecord:
        clean_title = str(title or "").strip()[:500]
        if not clean_title:
            raise ValueError("title must be non-empty")
        cid = str(conversation_id or "").strip()
        if not cid:
            raise ValueError("conversation_id must be non-empty")
        iid = str(item_id).strip()[:64] if item_id and str(item_id).strip() else f"task-{uuid4().hex[:16]}"
        clean_status = _validate_status(status)
        now = self._now()

        values: dict[str, Any] = {
            "item_id": iid,
            "conversation_id": cid,
            "team_name": str(team_name).strip()[:64] if team_name and str(team_name).strip() else None,
            "title": clean_title,
            "description": str(description or ""),
            "status": clean_status,
            "owner_agent_id": str(owner_agent_id).strip()[:64] if owner_agent_id else None,
            "blocked_by": _clean_blocked_by(blocked_by),
            "lease_expires_at": _as_utc(lease_expires_at),
            "result_ref": str(result_ref)[:512] if result_ref is not None else None,
            "created_at": now,
            "updated_at": now,
        }

        t = task_board_items

        def _op(conn: Connection) -> TaskBoardItemRecord:
            conn.execute(insert(t).values(**values))
            row = conn.execute(select(t).where(t.c.item_id == iid)).mappings().one()
            return _row_to_record(row)

        return self._run_write_tx(_op)

    def get(
        self,
        item_id: str,
        *,
        conversation_id: str | None = None,
    ) -> TaskBoardItemRecord | None:
        iid = str(item_id or "").strip()
        if not iid:
            return None
        t = task_board_items
        conditions = [t.c.item_id == iid]
        if conversation_id is not None:
            conditions.append(t.c.conversation_id == str(conversation_id))
        with self._engine.connect() as conn:
            row = conn.execute(select(t).where(and_(*conditions))).mappings().first()
        return _row_to_record(row) if row is not None else None

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        status: str | None = None,
        team_name: str | None = None,
    ) -> list[TaskBoardItemRecord]:
        t = task_board_items
        conditions = [t.c.conversation_id == str(conversation_id)]
        if status is not None:
            conditions.append(t.c.status == _validate_status(status))
        if team_name is not None:
            conditions.append(t.c.team_name == str(team_name))
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(t)
                .where(and_(*conditions))
                .order_by(asc(t.c.created_at), asc(t.c.item_id))
            ).mappings().all()
        return [_row_to_record(r) for r in rows]

    def update(
        self,
        item_id: str,
        *,
        conversation_id: str,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        blocked_by: Sequence[str] | None = None,
        team_name: str | None = None,
        result_ref: str | None = None,
    ) -> TaskBoardItemRecord | None:
        iid = str(item_id or "").strip()
        cid = str(conversation_id or "").strip()
        if not iid or not cid:
            return None

        updates: dict[str, Any] = {"updated_at": self._now()}
        if title is not None:
            clean_title = str(title).strip()[:500]
            if not clean_title:
                raise ValueError("title must be non-empty")
            updates["title"] = clean_title
        if description is not None:
            updates["description"] = str(description)
        if status is not None:
            updates["status"] = _validate_status(status)
        if blocked_by is not None:
            updates["blocked_by"] = _clean_blocked_by(blocked_by)
        if team_name is not None:
            updates["team_name"] = str(team_name).strip()[:64] if str(team_name).strip() else None
        if result_ref is not None:
            updates["result_ref"] = str(result_ref)[:512]

        t = task_board_items

        def _op(conn: Connection) -> TaskBoardItemRecord | None:
            res = conn.execute(
                update(t)
                .where(and_(t.c.item_id == iid, t.c.conversation_id == cid))
                .values(**updates)
            )
            if int(res.rowcount or 0) == 0:
                return None
            row = conn.execute(
                select(t).where(and_(t.c.item_id == iid, t.c.conversation_id == cid))
            ).mappings().one()
            return _row_to_record(row)

        return self._run_write_tx(_op)

    def claim(
        self,
        item_id: str,
        agent_id: str,
        *,
        conversation_id: str,
        lease_seconds: int = 900,
        now: datetime | None = None,
    ) -> TaskBoardItemRecord | None:
        iid = str(item_id or "").strip()
        aid = str(agent_id or "").strip()[:64]
        cid = str(conversation_id or "").strip()
        if not iid or not aid or not cid:
            return None
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        ts = _as_utc(now) or self._now()
        lease_exp = ts + timedelta(seconds=int(lease_seconds))
        t = task_board_items

        def _op(conn: Connection) -> TaskBoardItemRecord | None:
            stmt = _CLAIM_SQLITE_SQL if conn.dialect.name == "sqlite" else _CLAIM_POSTGRES_SQL
            res = conn.execute(
                stmt,
                {
                    "item_id": iid,
                    "conversation_id": cid,
                    "agent_id": aid,
                    "lease_expires_at": lease_exp,
                    "now": ts,
                },
            )
            if int(res.rowcount or 0) != 1:
                return None
            row = conn.execute(
                select(t).where(and_(t.c.item_id == iid, t.c.conversation_id == cid))
            ).mappings().one()
            return _row_to_record(row)

        return self._run_write_tx(_op)

    def renew(
        self,
        item_id: str,
        agent_id: str,
        *,
        conversation_id: str,
        lease_seconds: int = 900,
        now: datetime | None = None,
    ) -> TaskBoardItemRecord | None:
        iid = str(item_id or "").strip()
        aid = str(agent_id or "").strip()[:64]
        cid = str(conversation_id or "").strip()
        if not iid or not aid or not cid:
            return None
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        ts = _as_utc(now) or self._now()
        lease_exp = ts + timedelta(seconds=int(lease_seconds))
        t = task_board_items

        def _op(conn: Connection) -> TaskBoardItemRecord | None:
            res = conn.execute(
                update(t)
                .where(
                    and_(
                        t.c.item_id == iid,
                        t.c.conversation_id == cid,
                        t.c.status == "in_progress",
                        t.c.owner_agent_id == aid,
                    )
                )
                .values(lease_expires_at=lease_exp, updated_at=ts)
            )
            if int(res.rowcount or 0) != 1:
                return None
            row = conn.execute(
                select(t).where(and_(t.c.item_id == iid, t.c.conversation_id == cid))
            ).mappings().one()
            return _row_to_record(row)

        return self._run_write_tx(_op)

    def complete(
        self,
        item_id: str,
        agent_id: str,
        *,
        conversation_id: str,
        result_ref: str | None = None,
        now: datetime | None = None,
    ) -> TaskBoardItemRecord | None:
        iid = str(item_id or "").strip()
        aid = str(agent_id or "").strip()[:64]
        cid = str(conversation_id or "").strip()
        if not iid or not aid or not cid:
            return None

        ts = _as_utc(now) or self._now()
        updates: dict[str, Any] = {
            "status": "done",
            "lease_expires_at": None,
            "updated_at": ts,
        }
        if result_ref is not None:
            updates["result_ref"] = str(result_ref)[:512]
        t = task_board_items

        def _op(conn: Connection) -> TaskBoardItemRecord | None:
            res = conn.execute(
                update(t)
                .where(
                    and_(
                        t.c.item_id == iid,
                        t.c.conversation_id == cid,
                        t.c.status == "in_progress",
                        t.c.owner_agent_id == aid,
                    )
                )
                .values(**updates)
            )
            if int(res.rowcount or 0) != 1:
                return None
            row = conn.execute(
                select(t).where(and_(t.c.item_id == iid, t.c.conversation_id == cid))
            ).mappings().one()
            return _row_to_record(row)

        return self._run_write_tx(_op)

    def expire_leases(
        self,
        *,
        now: datetime | None = None,
        conversation_id: str | None = None,
    ) -> int:
        ts = _as_utc(now) or self._now()
        t = task_board_items
        conditions = [
            t.c.status == "in_progress",
            t.c.lease_expires_at.is_not(None),
            t.c.lease_expires_at <= ts,
        ]
        if conversation_id is not None:
            conditions.append(t.c.conversation_id == str(conversation_id))

        def _op(conn: Connection) -> int:
            res = conn.execute(
                update(t)
                .where(and_(*conditions))
                .values(
                    status="pending",
                    owner_agent_id=None,
                    lease_expires_at=None,
                    updated_at=ts,
                )
            )
            return int(res.rowcount or 0)

        return int(self._run_write_tx(_op))


__all__ = [
    "TaskBoardItemRecord",
    "TaskBoardStore",
    "VALID_TASK_STATUSES",
]
