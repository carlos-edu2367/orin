"""Memory lifecycle service: audit events, atomic supersession, tombstones, restore, and purge (WS-D2)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine

from agentos.memory.models_write import compute_text_hash

from .schema import agent_memories, memory_events

VALID_MEMORY_EVENTS = frozenset({
    "created",
    "superseded",
    "updated",
    "proposed",
    "promoted",
    "quarantined",
    "approved",
    "rejected",
    "conflict",
    "forgotten",
    "restored",
    "expired",
})


class MemoryLifecycle:
    """Encapsulates state transitions and event auditing over ``agent_memories`` + ``memory_events``."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record_event(
        self,
        connection: Connection,
        *,
        memory_id: str,
        user_id: str,
        event: str,
        actor: str = "system",
        old_text: str | None = None,
        new_text: str | None = None,
        related_id: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        created_at: datetime | None = None,
    ) -> int:
        if event not in VALID_MEMORY_EVENTS:
            raise ValueError(f"unsupported memory event: {event!r}")
        ts = created_at or datetime.now(UTC)
        # D-RF4 privacy rule: forgotten events MUST NEVER store old_text (or new_text).
        effective_old = None if event == "forgotten" else (str(old_text)[:4000] if old_text is not None else None)
        effective_new = None if event == "forgotten" else (str(new_text)[:4000] if new_text is not None else None)
        result = connection.execute(
            insert(memory_events).values(
                memory_id=str(memory_id),
                user_id=str(user_id),
                event=event,
                old_text=effective_old,
                new_text=effective_new,
                related_id=str(related_id) if related_id else None,
                actor=str(actor or "system")[:80],
                conversation_id=str(conversation_id) if conversation_id else None,
                turn_id=str(turn_id) if turn_id else None,
                created_at=ts,
            )
        )
        pk = getattr(result, "inserted_primary_key", None)
        return int(pk[0]) if pk and pk[0] is not None else 0

    def supersede(
        self,
        connection: Connection,
        old_id: str,
        new_id: str,
        actor: str = "system",
        old_text: str | None = None,
        new_text: str | None = None,
        *,
        user_id: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Atomically supersede ``old_id`` with ``new_id`` within the caller's transaction."""
        clean_old = str(old_id or "").strip()
        clean_new = str(new_id or "").strip()
        if not clean_old or not clean_new or clean_old == clean_new:
            return False

        predicates = [
            agent_memories.c.memory_id == clean_old,
            agent_memories.c.superseded_by.is_(None),
            agent_memories.c.status == "active",
        ]
        if user_id is not None:
            predicates.append(agent_memories.c.user_id == str(user_id))

        old_row = connection.execute(select(agent_memories).where(*predicates)).mappings().first()
        if old_row is None:
            return False

        ts = now or datetime.now(UTC)
        resolved_user_id = str(old_row["user_id"])
        preserved_old_text = str(old_row["fact"] or "") if old_text is None else str(old_text)

        result = connection.execute(
            update(agent_memories)
            .where(
                agent_memories.c.memory_id == clean_old,
                agent_memories.c.user_id == resolved_user_id,
                agent_memories.c.superseded_by.is_(None),
                agent_memories.c.status == "active",
            )
            .values(
                status="superseded",
                superseded_by=clean_new,
                invalid_at=ts,
                updated_at=ts,
            )
        )
        if not result.rowcount:
            return False

        self.record_event(
            connection,
            memory_id=clean_old,
            user_id=resolved_user_id,
            event="superseded",
            actor=actor,
            old_text=preserved_old_text,
            new_text=new_text,
            related_id=clean_new,
            conversation_id=conversation_id,
            turn_id=turn_id,
            created_at=ts,
        )
        return True

    def tombstone(
        self,
        memory_id: str,
        user_id: str,
        *,
        actor: str = "user",
        scope_types: tuple[str, ...] | None = None,
        scope_type: str | None = None,
        project_id: str | None = None,
        check_scope: bool = False,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        now: datetime | None = None,
        connection: Connection | None = None,
    ) -> bool:
        """Mark a memory deleted, erase its text, preserve text_hash, and log ``forgotten`` without old_text."""
        clean_id = str(memory_id or "").strip()
        if not clean_id:
            return False

        def _run(conn: Connection) -> bool:
            predicates = [
                agent_memories.c.memory_id == clean_id,
                agent_memories.c.user_id == str(user_id),
                agent_memories.c.status != "deleted",
            ]
            if check_scope:
                if scope_types is not None:
                    predicates.append(agent_memories.c.scope_type.in_(scope_types))
                elif scope_type is not None:
                    predicates.append(agent_memories.c.scope_type == scope_type)
                if project_id is not None:
                    predicates.append(agent_memories.c.project_id == project_id)
                else:
                    predicates.append(agent_memories.c.project_id.is_(None))

            row = conn.execute(select(agent_memories).where(*predicates)).mappings().first()
            if row is None:
                return False

            ts = now or datetime.now(UTC)
            existing_hash = str(row.get("text_hash") or "").strip()
            preserved_hash = existing_hash or compute_text_hash(str(row.get("fact") or ""))

            result = conn.execute(
                update(agent_memories)
                .where(
                    agent_memories.c.memory_id == clean_id,
                    agent_memories.c.user_id == str(user_id),
                    agent_memories.c.status != "deleted",
                )
                .values(
                    status="deleted",
                    fact="",
                    text_hash=preserved_hash,
                    invalid_at=ts,
                    updated_at=ts,
                )
            )
            if not result.rowcount:
                return False

            self.record_event(
                conn,
                memory_id=clean_id,
                user_id=str(user_id),
                event="forgotten",
                actor=actor,
                old_text=None,
                new_text=None,
                conversation_id=conversation_id,
                turn_id=turn_id,
                created_at=ts,
            )
            return True

        if connection is not None:
            return _run(connection)
        with self._engine.begin() as conn:
            return _run(conn)

    def restore(
        self,
        memory_id: str,
        user_id: str,
        *,
        fact: str | None = None,
        actor: str = "user",
        conversation_id: str | None = None,
        turn_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Reactivate a memory if text is available; return a structured error for erased tombstones."""
        clean_id = str(memory_id or "").strip()
        if not clean_id:
            return {"ok": False, "error": "memory_not_found", "message": "Memória não encontrada."}

        ts = now or datetime.now(UTC)
        with self._engine.begin() as conn:
            row = conn.execute(
                select(agent_memories).where(
                    agent_memories.c.memory_id == clean_id,
                    agent_memories.c.user_id == str(user_id),
                )
            ).mappings().first()
            if row is None:
                return {"ok": False, "error": "memory_not_found", "message": "Memória não encontrada."}

            raw_text = str(fact if fact is not None else (row.get("fact") or "")).strip()
            if not raw_text:
                return {
                    "ok": False,
                    "error": "tombstone_text_erased",
                    "message": "Memória esquecida teve o texto apagado e não pode ser restaurada sem texto.",
                    "memory_id": clean_id,
                    "status": str(row.get("status") or "deleted"),
                }

            normalized = " ".join(raw_text.split())[:4000]
            new_hash = compute_text_hash(normalized)
            conn.execute(
                update(agent_memories)
                .where(
                    agent_memories.c.memory_id == clean_id,
                    agent_memories.c.user_id == str(user_id),
                )
                .values(
                    status="active",
                    fact=normalized,
                    text_hash=new_hash,
                    superseded_by=None,
                    invalid_at=None,
                    updated_at=ts,
                )
            )
            self.record_event(
                conn,
                memory_id=clean_id,
                user_id=str(user_id),
                event="restored",
                actor=actor,
                old_text=str(row.get("fact") or "") or None,
                new_text=normalized,
                conversation_id=conversation_id,
                turn_id=turn_id,
                created_at=ts,
            )
            return {
                "ok": True,
                "memory_id": clean_id,
                "status": "active",
                "fact": normalized,
            }

    def history(self, memory_id: str, user_id: str) -> list[dict[str, object]]:
        """Return chronological audit events for ``memory_id`` scoped strictly to ``user_id``."""
        clean_id = str(memory_id or "").strip()
        if not clean_id:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(memory_events)
                .where(
                    memory_events.c.memory_id == clean_id,
                    memory_events.c.user_id == str(user_id),
                )
                .order_by(memory_events.c.created_at.asc(), memory_events.c.id.asc())
            ).mappings().all()
        items: list[dict[str, object]] = []
        for row in rows:
            created = row.get("created_at")
            items.append({
                "id": int(row["id"]),
                "memory_id": str(row["memory_id"]),
                "user_id": str(row["user_id"]),
                "event": str(row["event"]),
                "old_text": row.get("old_text"),
                "new_text": row.get("new_text"),
                "related_id": row.get("related_id"),
                "actor": str(row["actor"]),
                "conversation_id": row.get("conversation_id"),
                "turn_id": row.get("turn_id"),
                "created_at": created.isoformat() if isinstance(created, datetime) else created,
            })
        return items

    def purge_tombstones(self, older_than: datetime) -> int:
        """Physically delete tombstones whose ``invalid_at`` (or fallback ``updated_at``) is older than ``older_than``."""
        with self._engine.begin() as conn:
            result = conn.execute(
                delete(agent_memories).where(
                    agent_memories.c.status == "deleted",
                    or_(
                        agent_memories.c.invalid_at < older_than,
                        (agent_memories.c.invalid_at.is_(None)) & (agent_memories.c.updated_at < older_than),
                    ),
                )
            )
        return int(result.rowcount or 0)

    def resolve_chain(
        self,
        memory_id: str,
        user_id: str,
        *,
        max_hops: int = 32,
    ) -> tuple[dict[str, object] | None, bool]:
        """Follow ``superseded_by`` links and return ``(terminal_row, followed_supersession)``."""
        clean_id = str(memory_id or "").strip()
        if not clean_id:
            return None, False

        visited: set[str] = set()
        current_id = clean_id
        followed = False
        last_row: dict[str, object] | None = None

        with self._engine.connect() as conn:
            for _ in range(max_hops):
                if current_id in visited:
                    break
                visited.add(current_id)
                row = conn.execute(
                    select(agent_memories).where(
                        agent_memories.c.memory_id == current_id,
                        agent_memories.c.user_id == str(user_id),
                    )
                ).mappings().first()
                if row is None:
                    return last_row, followed
                row_dict = dict(row)
                last_row = row_dict
                next_id = str(row_dict.get("superseded_by") or "").strip()
                status = str(row_dict.get("status") or "active")
                if (status == "superseded" or next_id) and next_id and next_id not in visited:
                    followed = True
                    current_id = next_id
                    continue
                return row_dict, followed
        return last_row, followed

    def get_active_successor(
        self,
        memory_id: str,
        user_id: str,
        *,
        max_hops: int = 32,
    ) -> dict[str, object] | None:
        """Return the latest active row in the supersession chain starting at ``memory_id``."""
        terminal, _ = self.resolve_chain(memory_id, user_id, max_hops=max_hops)
        if terminal is None:
            return None
        if str(terminal.get("status") or "active") != "active":
            return None
        if terminal.get("superseded_by") is not None:
            return None
        return terminal


__all__ = ["MemoryLifecycle", "VALID_MEMORY_EVENTS"]
