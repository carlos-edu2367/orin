"""Shared per-conversation task board with atomic claim, leases and dependencies (WS-B3, B-RF3)."""
from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import Engine

from agentos.persistence.postgres.task_board import (
    VALID_TASK_STATUSES,
    TaskBoardItemRecord,
    TaskBoardStore,
)

DEFAULT_LEASE_SECONDS = 900  # 15 minutes
DEFAULT_RENEW_THROTTLE_SECONDS = 5.0


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _clean_blocked_by(blocked_by: Sequence[str] | None) -> tuple[str, ...]:
    if not blocked_by:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for raw in blocked_by:
        cleaned = str(raw or "").strip()[:64]
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return tuple(out)


def normalize_board_status(status: str) -> str:
    """Map tool-facing statuses (e.g. ``completed``/``deleted``) to board table statuses."""
    cleaned = str(status or "").strip().lower()
    if cleaned == "completed":
        return "done"
    if cleaned == "deleted":
        return "cancelled"
    if cleaned in VALID_TASK_STATUSES:
        return cleaned
    allowed = ", ".join(sorted(VALID_TASK_STATUSES | {"completed", "deleted"}))
    raise ValueError(f"Invalid task status {status!r}; expected one of: {allowed}")


def to_tool_status(board_status: str) -> str:
    """Map board table statuses (``done``/``cancelled``) back to tool-facing statuses."""
    cleaned = str(board_status or "").strip().lower()
    if cleaned == "done":
        return "completed"
    if cleaned == "cancelled":
        return "deleted"
    return cleaned


def record_to_task_dict(record: TaskBoardItemRecord) -> dict[str, Any]:
    """Serialize a ``TaskBoardItemRecord`` to JSON-safe tool & API task shape."""
    tool_status = to_tool_status(record.status)
    entry: dict[str, Any] = {
        "id": record.item_id,
        "item_id": record.item_id,
        "conversation_id": record.conversation_id,
        "subject": record.title,
        "content": record.title,
        "title": record.title,
        "description": record.description,
        "status": tool_status,
        "board_status": record.status,
        "owner": record.owner_agent_id,
        "owner_agent_id": record.owner_agent_id,
        "blocked_by": list(record.blocked_by),
        "team_name": record.team_name,
        "lease_expires_at": (
            record.lease_expires_at.isoformat() if record.lease_expires_at is not None else None
        ),
        "result_ref": record.result_ref,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }
    return entry


class _InMemoryTaskBoardStore:
    """Thread-safe in-memory mirror of ``TaskBoardStore`` for pure unit tests and engine-less sessions."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._items: dict[tuple[str, str], TaskBoardItemRecord] = {}

    def _now(self) -> datetime:
        return _as_utc(self._clock()) or datetime.now(UTC)

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
        clean_status = normalize_board_status(status)
        iid = str(item_id or uuid4().hex[:16]).strip()[:64]
        clean_team = str(team_name).strip()[:64] if team_name is not None and str(team_name).strip() else None
        clean_owner = str(owner_agent_id).strip()[:64] if owner_agent_id is not None and str(owner_agent_id).strip() else None
        clean_result = str(result_ref)[:512] if result_ref is not None else None
        now = self._now()
        record = TaskBoardItemRecord(
            item_id=iid,
            conversation_id=cid,
            team_name=clean_team,
            title=clean_title,
            description=str(description or ""),
            status=clean_status,
            owner_agent_id=clean_owner,
            blocked_by=_clean_blocked_by(blocked_by),
            lease_expires_at=_as_utc(lease_expires_at),
            result_ref=clean_result,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._items[(cid, iid)] = record
        return record

    def get(self, item_id: str, *, conversation_id: str) -> TaskBoardItemRecord | None:
        iid = str(item_id or "").strip()
        cid = str(conversation_id or "").strip()
        if not iid or not cid:
            return None
        with self._lock:
            return self._items.get((cid, iid))

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        status: str | None = None,
        team_name: str | None = None,
    ) -> list[TaskBoardItemRecord]:
        cid = str(conversation_id or "").strip()
        if not cid:
            return []
        clean_status = normalize_board_status(status) if status is not None else None
        clean_team = str(team_name) if team_name is not None else None
        with self._lock:
            rows = [
                rec
                for (rec_cid, _), rec in self._items.items()
                if rec_cid == cid
                and (clean_status is None or rec.status == clean_status)
                and (clean_team is None or rec.team_name == clean_team)
            ]
        rows.sort(key=lambda r: (r.created_at, r.item_id))
        return rows

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
        with self._lock:
            existing = self._items.get((cid, iid))
            if existing is None:
                return None
            new_title = existing.title
            if title is not None:
                clean_title = str(title).strip()[:500]
                if not clean_title:
                    raise ValueError("title must be non-empty")
                new_title = clean_title
            new_desc = str(description) if description is not None else existing.description
            new_status = normalize_board_status(status) if status is not None else existing.status
            new_blocked = _clean_blocked_by(blocked_by) if blocked_by is not None else existing.blocked_by
            new_team = existing.team_name
            if team_name is not None:
                new_team = str(team_name).strip()[:64] if str(team_name).strip() else None
            new_result = str(result_ref)[:512] if result_ref is not None else existing.result_ref
            updated = TaskBoardItemRecord(
                item_id=existing.item_id,
                conversation_id=existing.conversation_id,
                team_name=new_team,
                title=new_title,
                description=new_desc,
                status=new_status,
                owner_agent_id=existing.owner_agent_id,
                blocked_by=new_blocked,
                lease_expires_at=existing.lease_expires_at,
                result_ref=new_result,
                created_at=existing.created_at,
                updated_at=self._now(),
            )
            self._items[(cid, iid)] = updated
            return updated

    def claim(
        self,
        item_id: str,
        agent_id: str,
        *,
        conversation_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
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
        with self._lock:
            existing = self._items.get((cid, iid))
            if existing is None or existing.status != "pending" or existing.owner_agent_id is not None:
                return None
            for dep_id in existing.blocked_by:
                blocker = self._items.get((cid, dep_id))
                if blocker is None or blocker.status != "done":
                    return None
            claimed = TaskBoardItemRecord(
                item_id=existing.item_id,
                conversation_id=existing.conversation_id,
                team_name=existing.team_name,
                title=existing.title,
                description=existing.description,
                status="in_progress",
                owner_agent_id=aid,
                blocked_by=existing.blocked_by,
                lease_expires_at=lease_exp,
                result_ref=existing.result_ref,
                created_at=existing.created_at,
                updated_at=ts,
            )
            self._items[(cid, iid)] = claimed
            return claimed

    def renew(
        self,
        item_id: str,
        agent_id: str,
        *,
        conversation_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
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
        with self._lock:
            existing = self._items.get((cid, iid))
            if existing is None or existing.status != "in_progress" or existing.owner_agent_id != aid:
                return None
            renewed = TaskBoardItemRecord(
                item_id=existing.item_id,
                conversation_id=existing.conversation_id,
                team_name=existing.team_name,
                title=existing.title,
                description=existing.description,
                status=existing.status,
                owner_agent_id=existing.owner_agent_id,
                blocked_by=existing.blocked_by,
                lease_expires_at=lease_exp,
                result_ref=existing.result_ref,
                created_at=existing.created_at,
                updated_at=ts,
            )
            self._items[(cid, iid)] = renewed
            return renewed

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
        with self._lock:
            existing = self._items.get((cid, iid))
            if existing is None or existing.status != "in_progress" or existing.owner_agent_id != aid:
                return None
            done = TaskBoardItemRecord(
                item_id=existing.item_id,
                conversation_id=existing.conversation_id,
                team_name=existing.team_name,
                title=existing.title,
                description=existing.description,
                status="done",
                owner_agent_id=existing.owner_agent_id,
                blocked_by=existing.blocked_by,
                lease_expires_at=None,
                result_ref=str(result_ref)[:512] if result_ref is not None else existing.result_ref,
                created_at=existing.created_at,
                updated_at=ts,
            )
            self._items[(cid, iid)] = done
            return done

    def expire_leases(
        self,
        *,
        now: datetime | None = None,
        conversation_id: str | None = None,
    ) -> int:
        ts = _as_utc(now) or self._now()
        cid_filter = str(conversation_id) if conversation_id is not None else None
        expired = 0
        with self._lock:
            for key, rec in list(self._items.items()):
                if cid_filter is not None and rec.conversation_id != cid_filter:
                    continue
                if rec.status == "in_progress" and rec.lease_expires_at is not None and rec.lease_expires_at <= ts:
                    self._items[key] = TaskBoardItemRecord(
                        item_id=rec.item_id,
                        conversation_id=rec.conversation_id,
                        team_name=rec.team_name,
                        title=rec.title,
                        description=rec.description,
                        status="pending",
                        owner_agent_id=None,
                        blocked_by=rec.blocked_by,
                        lease_expires_at=None,
                        result_ref=rec.result_ref,
                        created_at=rec.created_at,
                        updated_at=ts,
                    )
                    expired += 1
        return expired


class TaskBoard:
    """Conversation-scoped shared task board wrapping ``TaskBoardStore`` (or thread-safe memory fallback)."""

    def __init__(
        self,
        *,
        conversation_id: str,
        engine: Engine | None = None,
        store: TaskBoardStore | _InMemoryTaskBoardStore | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        renew_throttle_seconds: float = DEFAULT_RENEW_THROTTLE_SECONDS,
    ) -> None:
        cid = str(conversation_id or "").strip()
        if not cid:
            raise ValueError("conversation_id must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.conversation_id = cid
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lease_seconds = int(lease_seconds)
        self._renew_throttle_seconds = max(0.0, float(renew_throttle_seconds))
        if store is not None:
            self._store: TaskBoardStore | _InMemoryTaskBoardStore = store
        elif engine is not None:
            self._store = TaskBoardStore(engine, clock=self._clock)
        else:
            self._store = _InMemoryTaskBoardStore(clock=self._clock)
        self._renew_lock = threading.Lock()
        self._last_renewed_at: dict[str, datetime] = {}

    def _now(self) -> datetime:
        return _as_utc(self._clock()) or datetime.now(UTC)

    def expire_leases(self, *, now: datetime | None = None) -> int:
        return self._store.expire_leases(
            now=_as_utc(now) or self._now(),
            conversation_id=self.conversation_id,
        )

    def create(
        self,
        *,
        title: str,
        description: str = "",
        item_id: str | None = None,
        team_name: str | None = None,
        status: str = "pending",
        owner_agent_id: str | None = None,
        blocked_by: Sequence[str] = (),
        result_ref: str | None = None,
    ) -> TaskBoardItemRecord:
        board_status = normalize_board_status(status)
        lease_exp = (
            self._now() + timedelta(seconds=self._lease_seconds)
            if board_status == "in_progress" and owner_agent_id
            else None
        )
        return self._store.create(
            conversation_id=self.conversation_id,
            title=title,
            description=description,
            item_id=item_id,
            team_name=team_name,
            status=board_status,
            owner_agent_id=owner_agent_id,
            blocked_by=blocked_by,
            lease_expires_at=lease_exp,
            result_ref=result_ref,
        )

    def get(self, item_id: str) -> TaskBoardItemRecord | None:
        self.expire_leases()
        return self._store.get(item_id, conversation_id=self.conversation_id)

    def list_items(
        self,
        *,
        status: str | None = None,
        team_name: str | None = None,
        include_cancelled: bool = False,
    ) -> list[TaskBoardItemRecord]:
        self.expire_leases()
        board_status = normalize_board_status(status) if status is not None and str(status).strip() else None
        items = self._store.list_for_conversation(
            self.conversation_id,
            status=board_status,
            team_name=team_name,
        )
        if board_status is None and not include_cancelled:
            return [item for item in items if item.status != "cancelled"]
        return items

    def update(
        self,
        item_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        blocked_by: Sequence[str] | None = None,
        add_blocked_by: Sequence[str] | None = None,
        add_blocks: Sequence[str] | None = None,
        team_name: str | None = None,
        result_ref: str | None = None,
        acting_agent_id: str | None = None,
    ) -> TaskBoardItemRecord | None:
        self.expire_leases()
        existing = self._store.get(item_id, conversation_id=self.conversation_id)
        if existing is None:
            return None

        merged_blocked: list[str] | None = None
        if blocked_by is not None:
            merged_blocked = list(_clean_blocked_by(blocked_by))
        if add_blocked_by:
            base = list(merged_blocked if merged_blocked is not None else existing.blocked_by)
            for dep in add_blocked_by:
                dep_id = str(dep or "").strip()[:64]
                if dep_id and dep_id != existing.item_id and dep_id not in base:
                    base.append(dep_id)
            merged_blocked = base

        board_status = normalize_board_status(status) if status is not None else None
        clean_actor = str(acting_agent_id or "").strip()[:64] or None

        updated: TaskBoardItemRecord | None
        if (
            board_status == "done"
            and clean_actor
            and existing.status == "in_progress"
            and existing.owner_agent_id == clean_actor
        ):
            if any(val is not None for val in (title, description, merged_blocked, team_name)):
                self._store.update(
                    existing.item_id,
                    conversation_id=self.conversation_id,
                    title=title,
                    description=description,
                    blocked_by=merged_blocked,
                    team_name=team_name,
                )
            updated = self._store.complete(
                existing.item_id,
                clean_actor,
                conversation_id=self.conversation_id,
                result_ref=result_ref,
                now=self._now(),
            )
            if updated is None:
                updated = self._store.update(
                    existing.item_id,
                    conversation_id=self.conversation_id,
                    status="done",
                    result_ref=result_ref,
                )
        else:
            updated = self._store.update(
                existing.item_id,
                conversation_id=self.conversation_id,
                title=title,
                description=description,
                status=board_status,
                blocked_by=merged_blocked,
                team_name=team_name,
                result_ref=result_ref,
            )

        if updated is not None and add_blocks:
            for raw_target in add_blocks:
                target_id = str(raw_target or "").strip()[:64]
                if not target_id or target_id == updated.item_id:
                    continue
                target = self._store.get(target_id, conversation_id=self.conversation_id)
                if target is not None and updated.item_id not in target.blocked_by:
                    self._store.update(
                        target.item_id,
                        conversation_id=self.conversation_id,
                        blocked_by=[*target.blocked_by, updated.item_id],
                    )
        return updated

    def claim(
        self,
        item_id: str,
        agent_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> TaskBoardItemRecord | None:
        self.expire_leases()
        claimed = self._store.claim(
            item_id,
            agent_id,
            conversation_id=self.conversation_id,
            lease_seconds=int(lease_seconds) if lease_seconds is not None else self._lease_seconds,
            now=self._now(),
        )
        if claimed is not None:
            aid = str(agent_id or "").strip()[:64]
            with self._renew_lock:
                self._last_renewed_at.pop(aid, None)
        return claimed

    def explain_claim_failure(self, item_id: str, agent_id: str) -> tuple[str, str]:
        """Diagnose why an atomic claim returned None so the tool reports a clear error."""
        del agent_id
        iid = str(item_id or "").strip()
        item = self._store.get(iid, conversation_id=self.conversation_id)
        if item is None:
            return ("TASK_NOT_FOUND", f"Task '{iid}' not found in this conversation.")
        if item.status == "in_progress" or item.owner_agent_id is not None:
            owner = item.owner_agent_id or "another agent"
            return ("TASK_ALREADY_CLAIMED", f"Task '{iid}' is already claimed by '{owner}'.")
        if item.status in {"done", "cancelled"}:
            return ("TASK_NOT_CLAIMABLE", f"Task '{iid}' has status '{to_tool_status(item.status)}' and cannot be claimed.")
        if item.blocked_by:
            unresolved: list[str] = []
            for dep_id in item.blocked_by:
                blocker = self._store.get(dep_id, conversation_id=self.conversation_id)
                if blocker is None or blocker.status != "done":
                    unresolved.append(dep_id)
            if unresolved:
                deps = ", ".join(unresolved)
                return ("TASK_BLOCKED", f"Task '{iid}' is blocked by unfinished dependencies: {deps}.")
        return ("TASK_NOT_CLAIMABLE", f"Task '{iid}' is not claimable (status='{item.status}').")

    def renew(
        self,
        item_id: str,
        agent_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> TaskBoardItemRecord | None:
        return self._store.renew(
            item_id,
            agent_id,
            conversation_id=self.conversation_id,
            lease_seconds=int(lease_seconds) if lease_seconds is not None else self._lease_seconds,
            now=self._now(),
        )

    def renew_owner_leases(
        self,
        agent_id: str,
        *,
        lease_seconds: int | None = None,
        force: bool = False,
    ) -> int:
        """Extend leases on all active ``in_progress`` items owned by ``agent_id`` (lightly throttled)."""
        aid = str(agent_id or "").strip()[:64]
        if not aid:
            return 0
        now = self._now()
        with self._renew_lock:
            last = self._last_renewed_at.get(aid)
            if (
                not force
                and last is not None
                and self._renew_throttle_seconds > 0
                and (now - last).total_seconds() < self._renew_throttle_seconds
            ):
                return 0
            self._last_renewed_at[aid] = now

        duration = int(lease_seconds) if lease_seconds is not None else self._lease_seconds
        active_items = self._store.list_for_conversation(self.conversation_id, status="in_progress")
        renewed = 0
        for item in active_items:
            if item.owner_agent_id != aid:
                continue
            if (
                self._store.renew(
                    item.item_id,
                    aid,
                    conversation_id=self.conversation_id,
                    lease_seconds=duration,
                    now=now,
                )
                is not None
            ):
                renewed += 1
        return renewed

    renew_for_agent = renew_owner_leases

    def complete(
        self,
        item_id: str,
        agent_id: str,
        *,
        result_ref: str | None = None,
    ) -> TaskBoardItemRecord | None:
        return self._store.complete(
            item_id,
            agent_id,
            conversation_id=self.conversation_id,
            result_ref=result_ref,
            now=self._now(),
        )


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_RENEW_THROTTLE_SECONDS",
    "TaskBoard",
    "normalize_board_status",
    "record_to_task_dict",
    "to_tool_status",
]
