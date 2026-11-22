"""Durable SQL store for subagent runs (WS-B0, Spec 6.3)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import random
import time
from typing import Any

from sqlalchemy import and_, asc, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from .schema import agent_runs

VALID_AGENT_RUN_STATES: frozenset[str] = frozenset(
    {"queued", "running", "stalled", "interrupted", "completed", "failed", "cancelled"}
)
_INTERRUPTABLE_BOOT_STATES: tuple[str, ...] = ("running", "stalled")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_state(state: str) -> str:
    cleaned = str(state or "").strip().lower()
    if cleaned not in VALID_AGENT_RUN_STATES:
        allowed = ", ".join(sorted(VALID_AGENT_RUN_STATES))
        raise ValueError(f"Invalid agent run state {state!r}; expected one of: {allowed}")
    return cleaned


def _to_decimal(value: float | int | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class AgentRunRecord:
    agent_id: str
    user_id: str
    conversation_id: str
    turn_id: str
    parent_agent_id: str | None
    subagent_type: str
    provider: str | None
    model_id: str | None
    effort: str | None
    state: str
    task_digest: str
    history_path: str | None
    worktree_path: str | None
    pipeline_run_id: str | None
    last_activity_at: datetime
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    result_ref: str | None
    created_at: datetime
    finished_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "parent_agent_id": self.parent_agent_id,
            "subagent_type": self.subagent_type,
            "provider": self.provider,
            "model_id": self.model_id,
            "effort": self.effort,
            "state": self.state,
            "task_digest": self.task_digest,
            "history_path": self.history_path,
            "worktree_path": self.worktree_path,
            "pipeline_run_id": self.pipeline_run_id,
            "last_activity_at": self.last_activity_at,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "result_ref": self.result_ref,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


def _row_to_record(row: Mapping[str, Any]) -> AgentRunRecord:
    raw_cost = row["cost_usd"]
    created = _as_utc(row["created_at"]) or datetime.now(UTC)
    last_activity = _as_utc(row["last_activity_at"]) or created
    return AgentRunRecord(
        agent_id=str(row["agent_id"]),
        user_id=str(row["user_id"]),
        conversation_id=str(row["conversation_id"]),
        turn_id=str(row["turn_id"]),
        parent_agent_id=str(row["parent_agent_id"]) if row["parent_agent_id"] is not None else None,
        subagent_type=str(row["subagent_type"]),
        provider=str(row["provider"]) if row["provider"] is not None else None,
        model_id=str(row["model_id"]) if row["model_id"] is not None else None,
        effort=str(row["effort"]) if row["effort"] is not None else None,
        state=str(row["state"]),
        task_digest=str(row["task_digest"] or ""),
        history_path=str(row["history_path"]) if row["history_path"] is not None else None,
        worktree_path=str(row["worktree_path"]) if row["worktree_path"] is not None else None,
        pipeline_run_id=str(row["pipeline_run_id"]) if row["pipeline_run_id"] is not None else None,
        last_activity_at=last_activity,
        tool_calls=int(row["tool_calls"] or 0),
        input_tokens=int(row["input_tokens"] or 0),
        output_tokens=int(row["output_tokens"] or 0),
        cost_usd=float(raw_cost) if raw_cost is not None else None,
        result_ref=str(row["result_ref"]) if row["result_ref"] is not None else None,
        created_at=created,
        finished_at=_as_utc(row["finished_at"]),
    )


class AgentRunStore:
    """Durable source of truth for subagent executions (``agent_runs``)."""

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

    def upsert(
        self,
        record: AgentRunRecord | None = None,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        parent_agent_id: str | None = None,
        subagent_type: str = "general-purpose",
        provider: str | None = None,
        model_id: str | None = None,
        effort: str | None = None,
        state: str = "queued",
        task_digest: str = "",
        history_path: str | None = None,
        worktree_path: str | None = None,
        pipeline_run_id: str | None = None,
        last_activity_at: datetime | None = None,
        tool_calls: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | int | Decimal | None = None,
        result_ref: str | None = None,
        created_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> AgentRunRecord:
        now = self._now()
        if record is not None:
            aid = str(record.agent_id).strip()[:255]
            uid = str(record.user_id)
            cid = str(record.conversation_id)
            tid = str(record.turn_id)
            paid = str(record.parent_agent_id) if record.parent_agent_id is not None else None
            stype = str(record.subagent_type)[:64]
            prov = str(record.provider)[:64] if record.provider is not None else None
            mid = str(record.model_id)[:128] if record.model_id is not None else None
            eff = str(record.effort)[:16] if record.effort is not None else None
            clean_state = _validate_state(record.state)
            digest = str(record.task_digest or "")[:64]
            hpath = str(record.history_path)[:512] if record.history_path is not None else None
            wpath = str(record.worktree_path)[:512] if record.worktree_path is not None else None
            prid = str(record.pipeline_run_id)[:64] if record.pipeline_run_id is not None else None
            c_at = _as_utc(record.created_at) or now
            la_at = _as_utc(record.last_activity_at) or c_at
            f_at = _as_utc(record.finished_at)
            tcalls = max(0, int(record.tool_calls))
            in_tok = max(0, int(record.input_tokens))
            out_tok = max(0, int(record.output_tokens))
            cost_val = _to_decimal(record.cost_usd)
            rref = str(record.result_ref)[:512] if record.result_ref is not None else None
        else:
            if not agent_id or not user_id or not conversation_id or not turn_id:
                raise ValueError("agent_id, user_id, conversation_id and turn_id are required")
            aid = str(agent_id).strip()[:255]
            uid = str(user_id)
            cid = str(conversation_id)
            tid = str(turn_id)
            paid = str(parent_agent_id) if parent_agent_id is not None else None
            stype = str(subagent_type)[:64]
            prov = str(provider)[:64] if provider is not None else None
            mid = str(model_id)[:128] if model_id is not None else None
            eff = str(effort)[:16] if effort is not None else None
            clean_state = _validate_state(state)
            digest = str(task_digest or "")[:64]
            hpath = str(history_path)[:512] if history_path is not None else None
            wpath = str(worktree_path)[:512] if worktree_path is not None else None
            prid = str(pipeline_run_id)[:64] if pipeline_run_id is not None else None
            c_at = _as_utc(created_at) or now
            la_at = _as_utc(last_activity_at) or c_at
            f_at = _as_utc(finished_at)
            tcalls = max(0, int(tool_calls or 0))
            in_tok = max(0, int(input_tokens or 0))
            out_tok = max(0, int(output_tokens or 0))
            cost_val = _to_decimal(cost_usd)
            rref = str(result_ref)[:512] if result_ref is not None else None

        values: dict[str, Any] = {
            "agent_id": aid,
            "user_id": uid,
            "conversation_id": cid,
            "turn_id": tid,
            "parent_agent_id": paid,
            "subagent_type": stype,
            "provider": prov,
            "model_id": mid,
            "effort": eff,
            "state": clean_state,
            "task_digest": digest,
            "history_path": hpath,
            "worktree_path": wpath,
            "pipeline_run_id": prid,
            "last_activity_at": la_at,
            "tool_calls": tcalls,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": cost_val,
            "result_ref": rref,
            "created_at": c_at,
            "finished_at": f_at,
        }

        t = agent_runs
        with self._engine.begin() as conn:
            existing = conn.execute(select(t.c.agent_id).where(t.c.agent_id == aid)).scalar()
            if existing is None:
                conn.execute(insert(t).values(**values))
            else:
                update_values = {k: v for k, v in values.items() if k != "agent_id"}
                conn.execute(update(t).where(t.c.agent_id == aid).values(**update_values))
            row = conn.execute(select(t).where(t.c.agent_id == aid)).mappings().one()
        return _row_to_record(row)

    def get(self, agent_id: str) -> AgentRunRecord | None:
        aid = str(agent_id).strip()
        if not aid:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                select(agent_runs).where(agent_runs.c.agent_id == aid)
            ).mappings().first()
        return _row_to_record(row) if row is not None else None

    def touch(
        self,
        agent_id: str,
        *,
        tool_calls: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        tokens: tuple[int, int] | Mapping[str, int] | None = None,
        cost_usd: float | int | Decimal | None = None,
        last_activity_at: datetime | None = None,
        state: str | None = None,
        result_ref: str | None = None,
        worktree_path: str | None = None,
        finished_at: datetime | None = None,
    ) -> AgentRunRecord | None:
        aid = str(agent_id).strip()
        if not aid:
            return None

        if tokens is not None:
            if isinstance(tokens, Mapping):
                if input_tokens is None and "input_tokens" in tokens:
                    input_tokens = int(tokens["input_tokens"])
                if output_tokens is None and "output_tokens" in tokens:
                    output_tokens = int(tokens["output_tokens"])
            else:
                if input_tokens is None:
                    input_tokens = int(tokens[0])
                if output_tokens is None:
                    output_tokens = int(tokens[1])

        updates: dict[str, Any] = {
            "last_activity_at": _as_utc(last_activity_at) or self._now(),
        }
        if tool_calls is not None:
            updates["tool_calls"] = max(0, int(tool_calls))
        if input_tokens is not None:
            updates["input_tokens"] = max(0, int(input_tokens))
        if output_tokens is not None:
            updates["output_tokens"] = max(0, int(output_tokens))
        if cost_usd is not None:
            updates["cost_usd"] = _to_decimal(cost_usd)
        if state is not None:
            updates["state"] = _validate_state(state)
        if result_ref is not None:
            updates["result_ref"] = str(result_ref)[:512]
        if worktree_path is not None:
            updates["worktree_path"] = str(worktree_path)[:512]
        if finished_at is not None:
            updates["finished_at"] = _as_utc(finished_at)

        t = agent_runs
        with self._engine.begin() as conn:
            res = conn.execute(update(t).where(t.c.agent_id == aid).values(**updates))
            if int(res.rowcount or 0) == 0:
                return None
            row = conn.execute(select(t).where(t.c.agent_id == aid)).mappings().one()
        return _row_to_record(row)

    def transition_state(
        self,
        agent_id: str,
        *,
        from_state: str,
        to_state: str,
        last_activity_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> AgentRunRecord | None:
        """Atomically transition ``agent_id`` from ``from_state`` to ``to_state`` (RT-B-13).

        Uses a single conditional ``UPDATE ... WHERE agent_id = :aid AND state = :from_state``
        so concurrent callers cannot double-dispatch a resume.
        """
        aid = str(agent_id).strip()
        if not aid:
            return None
        clean_from = _validate_state(from_state)
        clean_to = _validate_state(to_state)
        updates: dict[str, Any] = {
            "state": clean_to,
            "last_activity_at": _as_utc(last_activity_at) or self._now(),
        }
        if finished_at is not None:
            updates["finished_at"] = _as_utc(finished_at)

        t = agent_runs
        for attempt in range(8):
            try:
                with self._engine.begin() as conn:
                    res = conn.execute(
                        update(t)
                        .where(and_(t.c.agent_id == aid, t.c.state == clean_from))
                        .values(**updates)
                    )
                    if int(res.rowcount or 0) != 1:
                        return None
                    row = conn.execute(select(t).where(t.c.agent_id == aid)).mappings().one()
                return _row_to_record(row)
            except OperationalError as exc:
                msg = str(exc).lower()
                if ("locked" in msg or "busy" in msg) and attempt < 7:
                    time.sleep(0.01 * (attempt + 1) + random.uniform(0.0, 0.015))
                    continue
                raise
        return None

    def mark_interrupted_on_boot(
        self,
        *,
        now: datetime | None = None,
        include_queued: bool = False,
    ) -> int:
        stamp = _as_utc(now) or self._now()
        target_states = (*_INTERRUPTABLE_BOOT_STATES, "queued") if include_queued else _INTERRUPTABLE_BOOT_STATES
        t = agent_runs
        with self._engine.begin() as conn:
            res = conn.execute(
                update(t)
                .where(t.c.state.in_(target_states))
                .values(state="interrupted", finished_at=stamp)
            )
            return int(res.rowcount or 0)

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        user_id: str | None = None,
        state: str | None = None,
    ) -> list[AgentRunRecord]:
        t = agent_runs
        conditions = [t.c.conversation_id == str(conversation_id)]
        if user_id is not None:
            conditions.append(t.c.user_id == str(user_id))
        if state is not None:
            conditions.append(t.c.state == _validate_state(state))

        with self._engine.connect() as conn:
            rows = conn.execute(
                select(t)
                .where(and_(*conditions))
                .order_by(asc(t.c.created_at), asc(t.c.agent_id))
            ).mappings().all()
        return [_row_to_record(r) for r in rows]


__all__ = [
    "AgentRunRecord",
    "AgentRunStore",
    "VALID_AGENT_RUN_STATES",
]
