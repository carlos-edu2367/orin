"""SQLAlchemy backend for MemoryPromotionGate (WS-D5 / D-RF10).

Handles atomic promotion of ``scope_type='agent'`` proposals (resolving hash duplicates
against ``uq_agent_memories_active_hash`` and skipping unresolved conflicts) as well as
14-day expiration with audit events in ``memory_events``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agentos.memory.promotion import PromotionOutcome, ProposedMemoryRecord

from .memory_lifecycle import MemoryLifecycle
from .schema import agent_memories, memory_events


class SqlMemoryPromotionBackend:
    """Persistence adapter implementing ``PromotionBackend`` over SQLAlchemy."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._lifecycle = MemoryLifecycle(engine)

    def list_proposed_for_turn(
        self,
        turn_id: str,
        *,
        user_id: str | None = None,
    ) -> list[ProposedMemoryRecord]:
        clean_turn = str(turn_id or "").strip()
        if not clean_turn:
            return []

        predicates = [
            memory_events.c.turn_id == clean_turn,
            memory_events.c.event == "proposed",
            agent_memories.c.status == "proposed",
            agent_memories.c.scope_type == "agent",
            agent_memories.c.superseded_by.is_(None),
        ]
        if user_id is not None and str(user_id).strip():
            predicates.append(agent_memories.c.user_id == str(user_id).strip())

        stmt = (
            select(
                agent_memories.c.memory_id,
                agent_memories.c.user_id,
                agent_memories.c.scope_type,
                agent_memories.c.scope_id,
                agent_memories.c.project_id,
                agent_memories.c.agent_key,
                agent_memories.c.text_hash,
                agent_memories.c.fact,
            )
            .select_from(
                agent_memories.join(
                    memory_events,
                    (memory_events.c.memory_id == agent_memories.c.memory_id)
                    & (memory_events.c.user_id == agent_memories.c.user_id),
                )
            )
            .where(*predicates)
            .order_by(agent_memories.c.created_at.asc(), agent_memories.c.memory_id.asc())
        )
        seen: set[str] = set()
        records: list[ProposedMemoryRecord] = []
        with self._engine.connect() as conn:
            for row in conn.execute(stmt).mappings().all():
                mid = str(row["memory_id"])
                if mid in seen:
                    continue
                seen.add(mid)
                records.append(
                    ProposedMemoryRecord(
                        memory_id=mid,
                        user_id=str(row["user_id"]),
                        scope_type=str(row["scope_type"]),
                        scope_id=str(row["scope_id"] or ""),
                        project_id=str(row["project_id"]) if row.get("project_id") is not None else None,
                        agent_key=str(row["agent_key"]) if row.get("agent_key") is not None else None,
                        text_hash=str(row["text_hash"] or ""),
                        fact=str(row["fact"] or ""),
                    )
                )
        return records

    def promote_proposal(
        self,
        memory_id: str,
        *,
        actor: str,
        turn_id: str,
        now: datetime,
    ) -> PromotionOutcome:
        clean_id = str(memory_id or "").strip()
        if not clean_id:
            return PromotionOutcome(memory_id=clean_id, outcome="skipped")

        ts = now or datetime.now(UTC)
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    select(agent_memories).where(agent_memories.c.memory_id == clean_id)
                ).mappings().first()
                if row is None:
                    return PromotionOutcome(memory_id=clean_id, outcome="skipped")
                if str(row.get("status") or "") != "proposed" or str(row.get("scope_type") or "") != "agent":
                    return PromotionOutcome(memory_id=clean_id, outcome="skipped")

                # Skip proposals that have an open conflict event waiting for user review.
                has_conflict = conn.execute(
                    select(memory_events.c.id).where(
                        memory_events.c.memory_id == clean_id,
                        memory_events.c.user_id == str(row["user_id"]),
                        memory_events.c.event == "conflict",
                    ).limit(1)
                ).first()
                if has_conflict is not None:
                    return PromotionOutcome(memory_id=clean_id, outcome="skipped")

                proj_pred = (
                    agent_memories.c.project_id == row["project_id"]
                    if row.get("project_id") is not None
                    else agent_memories.c.project_id.is_(None)
                )
                agent_pred = (
                    agent_memories.c.agent_key == row["agent_key"]
                    if row.get("agent_key") is not None
                    else agent_memories.c.agent_key.is_(None)
                )
                existing_active = conn.execute(
                    select(agent_memories.c.memory_id).where(
                        agent_memories.c.user_id == str(row["user_id"]),
                        agent_memories.c.scope_type == "agent",
                        agent_memories.c.scope_id == str(row.get("scope_id") or ""),
                        proj_pred,
                        agent_pred,
                        agent_memories.c.text_hash == str(row.get("text_hash") or ""),
                        agent_memories.c.status == "active",
                        agent_memories.c.memory_id != clean_id,
                    ).limit(1)
                ).mappings().first()

                if existing_active is not None:
                    active_id = str(existing_active["memory_id"])
                    conn.execute(
                        update(agent_memories)
                        .where(agent_memories.c.memory_id == active_id)
                        .values(last_used_at=ts, updated_at=ts)
                    )
                    conn.execute(
                        update(agent_memories)
                        .where(
                            agent_memories.c.memory_id == clean_id,
                            agent_memories.c.status == "proposed",
                        )
                        .values(status="invalid", invalid_at=ts, updated_at=ts)
                    )
                    self._lifecycle.record_event(
                        conn,
                        memory_id=clean_id,
                        user_id=str(row["user_id"]),
                        event="rejected",
                        actor=actor,
                        old_text=str(row.get("fact") or "") or None,
                        related_id=active_id,
                        conversation_id=str(row["conversation_id"]) if row.get("conversation_id") else None,
                        turn_id=turn_id,
                        created_at=ts,
                    )
                    return PromotionOutcome(
                        memory_id=clean_id,
                        outcome="deduplicated",
                        active_memory_id=active_id,
                    )

                updated = conn.execute(
                    update(agent_memories)
                    .where(
                        agent_memories.c.memory_id == clean_id,
                        agent_memories.c.status == "proposed",
                    )
                    .values(
                        status="active",
                        valid_from=row.get("valid_from") or ts,
                        invalid_at=None,
                        updated_at=ts,
                    )
                )
                if not updated.rowcount:
                    return PromotionOutcome(memory_id=clean_id, outcome="skipped")

                self._lifecycle.record_event(
                    conn,
                    memory_id=clean_id,
                    user_id=str(row["user_id"]),
                    event="promoted",
                    actor=actor,
                    new_text=str(row.get("fact") or "") or None,
                    conversation_id=str(row["conversation_id"]) if row.get("conversation_id") else None,
                    turn_id=turn_id,
                    created_at=ts,
                )
                return PromotionOutcome(
                    memory_id=clean_id,
                    outcome="promoted",
                    active_memory_id=clean_id,
                )
        except IntegrityError:
            # Another process promoted an identical hash concurrently; mark this duplicate invalid.
            with self._engine.begin() as conn:
                row = conn.execute(
                    select(agent_memories).where(agent_memories.c.memory_id == clean_id)
                ).mappings().first()
                if row is not None and str(row.get("status") or "") == "proposed":
                    conn.execute(
                        update(agent_memories)
                        .where(agent_memories.c.memory_id == clean_id)
                        .values(status="invalid", invalid_at=ts, updated_at=ts)
                    )
                    self._lifecycle.record_event(
                        conn,
                        memory_id=clean_id,
                        user_id=str(row["user_id"]),
                        event="rejected",
                        actor=actor,
                        old_text=str(row.get("fact") or "") or None,
                        conversation_id=str(row["conversation_id"]) if row.get("conversation_id") else None,
                        turn_id=turn_id,
                        created_at=ts,
                    )
            return PromotionOutcome(memory_id=clean_id, outcome="deduplicated")

    def expire_proposals(
        self,
        older_than: datetime,
        *,
        now: datetime,
        actor: str = "system",
        scope_type: str | None = "agent",
    ) -> list[str]:
        ts = now or datetime.now(UTC)
        predicates = [
            agent_memories.c.status == "proposed",
            agent_memories.c.created_at < older_than,
        ]
        if scope_type is not None:
            predicates.append(agent_memories.c.scope_type == scope_type)

        expired_ids: list[str] = []
        with self._engine.begin() as conn:
            rows = conn.execute(select(agent_memories).where(*predicates)).mappings().all()
            for row in rows:
                mid = str(row["memory_id"])
                res = conn.execute(
                    update(agent_memories)
                    .where(
                        agent_memories.c.memory_id == mid,
                        agent_memories.c.status == "proposed",
                    )
                    .values(status="invalid", invalid_at=ts, updated_at=ts)
                )
                if not res.rowcount:
                    continue
                self._lifecycle.record_event(
                    conn,
                    memory_id=mid,
                    user_id=str(row["user_id"]),
                    event="expired",
                    actor=actor,
                    old_text=str(row.get("fact") or "") or None,
                    conversation_id=str(row["conversation_id"]) if row.get("conversation_id") else None,
                    created_at=ts,
                )
                expired_ids.append(mid)
        return expired_ids


__all__ = ["SqlMemoryPromotionBackend"]
