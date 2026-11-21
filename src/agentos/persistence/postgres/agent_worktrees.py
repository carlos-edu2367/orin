"""Durable SQL store for isolated subagent worktrees (WS-B0, Spec 6.3)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, asc, delete, insert, select, update
from sqlalchemy.engine import Engine

from .schema import agent_worktrees

VALID_MERGE_STATES: frozenset[str] = frozenset(
    {"pending", "applied", "conflict", "check_failed", "discarded", "empty"}
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_merge_state(merge_state: str) -> str:
    cleaned = str(merge_state or "").strip().lower()
    if cleaned not in VALID_MERGE_STATES:
        allowed = ", ".join(sorted(VALID_MERGE_STATES))
        raise ValueError(f"Invalid merge_state {merge_state!r}; expected one of: {allowed}")
    return cleaned


@dataclass(frozen=True, slots=True)
class AgentWorktreeRecord:
    agent_id: str
    workspace_root: str
    base_commit: str
    branch: str
    merge_state: str
    merge_detail: dict[str, Any] | None
    queued_at: datetime | None
    merged_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "workspace_root": self.workspace_root,
            "base_commit": self.base_commit,
            "branch": self.branch,
            "merge_state": self.merge_state,
            "merge_detail": dict(self.merge_detail) if isinstance(self.merge_detail, dict) else self.merge_detail,
            "queued_at": self.queued_at,
            "merged_at": self.merged_at,
        }


def _row_to_record(row: Mapping[str, Any]) -> AgentWorktreeRecord:
    raw_detail = row["merge_detail"]
    detail = dict(raw_detail) if isinstance(raw_detail, Mapping) else None
    return AgentWorktreeRecord(
        agent_id=str(row["agent_id"]),
        workspace_root=str(row["workspace_root"]),
        base_commit=str(row["base_commit"]),
        branch=str(row["branch"]),
        merge_state=str(row["merge_state"]),
        merge_detail=detail,
        queued_at=_as_utc(row["queued_at"]),
        merged_at=_as_utc(row["merged_at"]),
    )


class AgentWorktreeStore:
    """Durable store for subagent worktree lifecycle and merge queue status."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(UTC))

    def upsert(
        self,
        record: AgentWorktreeRecord | None = None,
        *,
        agent_id: str | None = None,
        workspace_root: str | None = None,
        base_commit: str | None = None,
        branch: str | None = None,
        merge_state: str = "pending",
        merge_detail: Mapping[str, Any] | None = None,
        queued_at: datetime | None = None,
        merged_at: datetime | None = None,
    ) -> AgentWorktreeRecord:
        if record is not None:
            aid = str(record.agent_id).strip()[:64]
            ws = str(record.workspace_root)[:1024]
            base = str(record.base_commit)[:64]
            br = str(record.branch)[:255]
            mstate = _validate_merge_state(record.merge_state)
            mdetail = dict(record.merge_detail) if isinstance(record.merge_detail, Mapping) else None
            q_at = _as_utc(record.queued_at)
            m_at = _as_utc(record.merged_at)
        else:
            if not agent_id or not workspace_root or not base_commit or not branch:
                raise ValueError("agent_id, workspace_root, base_commit and branch are required")
            aid = str(agent_id).strip()[:64]
            ws = str(workspace_root)[:1024]
            base = str(base_commit)[:64]
            br = str(branch)[:255]
            mstate = _validate_merge_state(merge_state)
            mdetail = dict(merge_detail) if isinstance(merge_detail, Mapping) else None
            q_at = _as_utc(queued_at)
            m_at = _as_utc(merged_at)

        values: dict[str, Any] = {
            "agent_id": aid,
            "workspace_root": ws,
            "base_commit": base,
            "branch": br,
            "merge_state": mstate,
            "merge_detail": mdetail,
            "queued_at": q_at,
            "merged_at": m_at,
        }

        t = agent_worktrees
        with self._engine.begin() as conn:
            existing = conn.execute(select(t.c.agent_id).where(t.c.agent_id == aid)).scalar()
            if existing is None:
                conn.execute(insert(t).values(**values))
            else:
                update_values = {k: v for k, v in values.items() if k != "agent_id"}
                conn.execute(update(t).where(t.c.agent_id == aid).values(**update_values))
            row = conn.execute(select(t).where(t.c.agent_id == aid)).mappings().one()
        return _row_to_record(row)

    create = upsert

    def get(self, agent_id: str) -> AgentWorktreeRecord | None:
        aid = str(agent_id).strip()[:64]
        if not aid:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                select(agent_worktrees).where(agent_worktrees.c.agent_id == aid)
            ).mappings().first()
        return _row_to_record(row) if row is not None else None

    def update_merge_state(
        self,
        agent_id: str,
        merge_state: str,
        *,
        merge_detail: Mapping[str, Any] | None = None,
        queued_at: datetime | None = None,
        merged_at: datetime | None = None,
    ) -> AgentWorktreeRecord | None:
        aid = str(agent_id).strip()[:64]
        if not aid:
            return None
        clean_state = _validate_merge_state(merge_state)
        updates: dict[str, Any] = {"merge_state": clean_state}
        if merge_detail is not None:
            updates["merge_detail"] = dict(merge_detail)
        if queued_at is not None:
            updates["queued_at"] = _as_utc(queued_at)
        if merged_at is not None:
            updates["merged_at"] = _as_utc(merged_at)

        t = agent_worktrees
        with self._engine.begin() as conn:
            res = conn.execute(update(t).where(t.c.agent_id == aid).values(**updates))
            if int(res.rowcount or 0) == 0:
                return None
            row = conn.execute(select(t).where(t.c.agent_id == aid)).mappings().one()
        return _row_to_record(row)

    def list_for_workspace(
        self,
        workspace_root: str,
        *,
        merge_state: str | None = None,
    ) -> list[AgentWorktreeRecord]:
        t = agent_worktrees
        conditions = [t.c.workspace_root == str(workspace_root)]
        if merge_state is not None:
            conditions.append(t.c.merge_state == _validate_merge_state(merge_state))
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(t).where(and_(*conditions)).order_by(asc(t.c.agent_id))
            ).mappings().all()
        return [_row_to_record(r) for r in rows]

    def delete(self, agent_id: str) -> bool:
        aid = str(agent_id).strip()[:64]
        if not aid:
            return False
        with self._engine.begin() as conn:
            res = conn.execute(delete(agent_worktrees).where(agent_worktrees.c.agent_id == aid))
            return int(res.rowcount or 0) > 0


__all__ = [
    "AgentWorktreeRecord",
    "AgentWorktreeStore",
    "VALID_MERGE_STATES",
]
