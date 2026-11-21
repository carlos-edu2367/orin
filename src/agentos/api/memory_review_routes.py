"""HTTP routes for memory review, conflict resolution, restore, and history (WS-D6)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from agentos.memory.models_write import compute_text_hash
from agentos.persistence.postgres.memory_lifecycle import MemoryLifecycle
from agentos.persistence.postgres.schema import agent_memories, memory_events

from .contracts import ApplicationNotFoundError, ApplicationValidationError
from .security import AuthenticatedPrincipal

VALID_STATUSES = frozenset({"active", "proposed", "quarantined", "superseded", "invalid", "deleted"})
PENDING_STATUSES = frozenset({"proposed", "quarantined"})
HISTORY_STATUSES = frozenset({"superseded", "invalid", "deleted"})
VALID_SCOPE_TYPES = frozenset({"user", "project", "agent", "friend"})

ConflictResolution = Literal["keep_new", "keep_old", "keep_both"]


class ApproveMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: ConflictResolution | None = None
    conflicting_memory_id: str | None = Field(default=None, max_length=255)


class ResolveConflictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: ConflictResolution = "keep_new"
    conflicting_memory_id: str | None = Field(default=None, max_length=255)


def _resolve_engine(services: Any) -> Engine:
    for attr in ("projects", "conversation_application", "local_workspaces", "context_observations"):
        port = getattr(services, attr, None)
        engine = getattr(port, "_engine", None) if port is not None else None
        if engine is not None:
            return engine
    quota_service = getattr(services, "quota_service", None)
    qstore = getattr(quota_service, "_store", None) if quota_service is not None else None
    engine = getattr(qstore, "_engine", None)
    if engine is not None:
        return engine
    raise RuntimeError("memory store engine is unavailable")


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def _serialize_memory(
    row: Mapping[str, Any],
    *,
    conflicting_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = str(row.get("status") or "active")
    fact = str(row.get("fact") or "")
    review_kind: str | None = None
    if status == "quarantined":
        review_kind = "quarantined"
    elif status == "proposed":
        review_kind = "conflict" if conflicting_memory is not None else "proposed"

    can_restore = status in HISTORY_STATUSES and bool(fact.strip())

    raw_tags = row.get("tags")
    tags = [str(item) for item in raw_tags] if isinstance(raw_tags, list) else []

    return {
        "memory_id": str(row["memory_id"]),
        "fact": fact,
        "tags": tags,
        "kind": str(row.get("kind") or "fact"),
        "scope_type": str(row.get("scope_type") or "user"),
        "scope_id": str(row.get("scope_id") or ""),
        "project_id": str(row["project_id"]) if row.get("project_id") is not None else None,
        "conversation_id": str(row["conversation_id"]) if row.get("conversation_id") is not None else None,
        "agent_key": str(row["agent_key"]) if row.get("agent_key") is not None else None,
        "status": status,
        "source": str(row.get("source") or "agent_learned"),
        "source_kind": str(row.get("source_kind") or "agent"),
        "source_ref": str(row["source_ref"]) if row.get("source_ref") is not None else None,
        "trust": int(row.get("trust") if row.get("trust") is not None else 1),
        "confidence": float(row.get("confidence") if row.get("confidence") is not None else 1.0),
        "superseded_by": str(row["superseded_by"]) if row.get("superseded_by") is not None else None,
        "valid_from": _iso(row.get("valid_from")),
        "invalid_at": _iso(row.get("invalid_at")),
        "hit_count": int(row.get("hit_count") or 0),
        "last_used_at": _iso(row.get("last_used_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "review_kind": review_kind,
        "can_restore": can_restore,
        "conflicting_memory": conflicting_memory,
    }


def _conflict_response(code: str, message: str, *, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        {
            "detail": message,
            "message": message,
            "error": {
                "code": code,
                "category": "CONFLICT",
                "message_key": code,
                "message": message,
                "retryable": False,
                "details": details or {},
            },
        },
        status_code=409,
    )


def _parse_status_filter(raw: str | None) -> set[str] | None:
    clean = (raw or "").strip().lower()
    if not clean or clean == "pending":
        return set(PENDING_STATUSES)
    if clean == "history":
        return set(HISTORY_STATUSES)
    if clean == "all":
        return None
    parts = {part.strip() for part in clean.split(",") if part.strip()}
    invalid = parts - VALID_STATUSES
    if invalid:
        raise ApplicationValidationError(f"invalid status filter: {', '.join(sorted(invalid))}")
    return parts


def _latest_conflict_targets(
    conn: Connection,
    *,
    user_id: str,
    memory_ids: list[str],
) -> dict[str, str]:
    if not memory_ids:
        return {}
    rows = conn.execute(
        select(memory_events.c.memory_id, memory_events.c.related_id)
        .where(
            memory_events.c.user_id == user_id,
            memory_events.c.memory_id.in_(memory_ids),
            memory_events.c.event == "conflict",
            memory_events.c.related_id.is_not(None),
        )
        .order_by(memory_events.c.created_at.desc(), memory_events.c.id.desc())
    ).mappings().all()
    mapping: dict[str, str] = {}
    for row in rows:
        mid = str(row["memory_id"])
        rel = str(row["related_id"] or "").strip()
        if rel and mid not in mapping:
            mapping[mid] = rel
    return mapping


def _list_review_payload(
    engine: Engine,
    *,
    user_id: str,
    project_id: str | None = None,
    agent_key: str | None = None,
    status: str | None = None,
    scope_type: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    statuses = _parse_status_filter(status)
    clean_project = (project_id or "").strip() or None
    clean_agent = (agent_key or "").strip() or None
    clean_scope = (scope_type or "").strip().lower() or None
    if clean_scope and clean_scope not in VALID_SCOPE_TYPES:
        raise ApplicationValidationError(f"invalid scope_type filter: {clean_scope}")
    clean_query = (query or "").strip().lower()
    bounded_limit = min(max(int(limit), 1), 500)

    base_predicates = [agent_memories.c.user_id == user_id]
    if clean_project is not None:
        base_predicates.append(agent_memories.c.project_id == clean_project)
    if clean_agent is not None:
        base_predicates.append(agent_memories.c.agent_key == clean_agent)
    if clean_scope is not None:
        base_predicates.append(agent_memories.c.scope_type == clean_scope)

    query_predicates = list(base_predicates)
    if statuses is not None:
        query_predicates.append(agent_memories.c.status.in_(sorted(statuses)))

    with engine.connect() as conn:
        rows = conn.execute(
            select(agent_memories)
            .where(*query_predicates)
            .order_by(agent_memories.c.updated_at.desc(), agent_memories.c.created_at.desc())
            .limit(bounded_limit if not clean_query else bounded_limit * 4)
        ).mappings().all()

        if clean_query:
            filtered_rows = []
            for row in rows:
                fact_text = str(row.get("fact") or "").lower()
                agent_text = str(row.get("agent_key") or "").lower()
                tags = row.get("tags") if isinstance(row.get("tags"), list) else []
                tags_text = " ".join(str(t).lower() for t in tags)
                if clean_query in fact_text or clean_query in agent_text or clean_query in tags_text:
                    filtered_rows.append(row)
                if len(filtered_rows) >= bounded_limit:
                    break
            rows = filtered_rows

        proposed_ids = [str(r["memory_id"]) for r in rows if str(r.get("status") or "") == "proposed"]
        conflict_targets = _latest_conflict_targets(conn, user_id=user_id, memory_ids=proposed_ids)
        related_ids = sorted(set(conflict_targets.values()))
        related_map: dict[str, dict[str, Any]] = {}
        if related_ids:
            rel_rows = conn.execute(
                select(agent_memories).where(
                    agent_memories.c.user_id == user_id,
                    agent_memories.c.memory_id.in_(related_ids),
                )
            ).mappings().all()
            for rel_row in rel_rows:
                related_map[str(rel_row["memory_id"])] = _serialize_memory(rel_row)

        items: list[dict[str, Any]] = []
        for row in rows:
            mid = str(row["memory_id"])
            target_id = conflict_targets.get(mid)
            conflicting = related_map.get(target_id) if target_id else None
            items.append(_serialize_memory(row, conflicting_memory=conflicting))

        # Summary counts scoped strictly to user_id (and optional project_id/agent_key)
        all_status_rows = conn.execute(
            select(agent_memories.c.memory_id, agent_memories.c.status).where(*base_predicates)
        ).mappings().all()
        counts = {
            "active": 0,
            "pending": 0,
            "proposed": 0,
            "quarantined": 0,
            "conflicts": 0,
            "history": 0,
        }
        all_proposed_ids: list[str] = []
        for srow in all_status_rows:
            st = str(srow.get("status") or "active")
            if st == "active":
                counts["active"] += 1
            elif st == "proposed":
                counts["proposed"] += 1
                counts["pending"] += 1
                all_proposed_ids.append(str(srow["memory_id"]))
            elif st == "quarantined":
                counts["quarantined"] += 1
                counts["pending"] += 1
            elif st in HISTORY_STATUSES:
                counts["history"] += 1
        if all_proposed_ids:
            all_conflicts = _latest_conflict_targets(conn, user_id=user_id, memory_ids=all_proposed_ids)
            counts["conflicts"] = len(all_conflicts)

        # Distinct agent_keys for this user (ignoring current agent_key filter so dropdown stays complete)
        agent_predicates = [
            agent_memories.c.user_id == user_id,
            agent_memories.c.agent_key.is_not(None),
        ]
        if clean_project is not None:
            agent_predicates.append(agent_memories.c.project_id == clean_project)
        agent_rows = conn.execute(
            select(agent_memories.c.agent_key).where(*agent_predicates).distinct()
        ).scalars().all()
        agent_keys = sorted({str(key).strip() for key in agent_rows if str(key or "").strip()})

    return {
        "items": items,
        "counts": counts,
        "agent_keys": agent_keys,
    }


def _find_active_hash_duplicate(
    conn: Connection,
    row: Mapping[str, Any],
    *,
    exclude_memory_id: str,
) -> Mapping[str, Any] | None:
    text_hash = str(row.get("text_hash") or "").strip() or compute_text_hash(str(row.get("fact") or ""))
    predicates = [
        agent_memories.c.user_id == str(row["user_id"]),
        agent_memories.c.scope_type == str(row["scope_type"]),
        agent_memories.c.scope_id == str(row.get("scope_id") or ""),
        agent_memories.c.text_hash == text_hash,
        agent_memories.c.status == "active",
        agent_memories.c.memory_id != exclude_memory_id,
    ]
    if row.get("project_id") is None:
        predicates.append(agent_memories.c.project_id.is_(None))
    else:
        predicates.append(agent_memories.c.project_id == str(row["project_id"]))
    if row.get("agent_key") is None:
        predicates.append(agent_memories.c.agent_key.is_(None))
    else:
        predicates.append(agent_memories.c.agent_key == str(row["agent_key"]))
    return conn.execute(select(agent_memories).where(and_(*predicates))).mappings().first()


def _memory_scope_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("scope_type") or ""),
        str(row.get("scope_id") or ""),
        str(row.get("project_id") or ""),
        str(row.get("agent_key") or ""),
    )


def _has_recorded_conflict_link(
    conn: Connection,
    *,
    user_id: str,
    memory_id: str,
    target_id: str,
) -> bool:
    hit = conn.execute(
        select(memory_events.c.id)
        .where(
            memory_events.c.user_id == user_id,
            memory_events.c.event == "conflict",
            (
                (memory_events.c.memory_id == memory_id) & (memory_events.c.related_id == target_id)
            )
            | (
                (memory_events.c.memory_id == target_id) & (memory_events.c.related_id == memory_id)
            ),
        )
        .limit(1)
    ).first()
    return hit is not None


def _approve_or_resolve(
    engine: Engine,
    *,
    user_id: str,
    memory_id: str,
    resolution: ConflictResolution | None = None,
    conflicting_memory_id: str | None = None,
) -> JSONResponse:
    clean_id = str(memory_id or "").strip()
    if not clean_id:
        raise ApplicationNotFoundError(memory_id)

    lifecycle = MemoryLifecycle(engine)
    actor = f"user:{user_id}"
    now = datetime.now(UTC)

    try:
        with engine.begin() as conn:
            row = conn.execute(
                select(agent_memories).where(
                    agent_memories.c.memory_id == clean_id,
                    agent_memories.c.user_id == user_id,
                )
            ).mappings().first()
            if row is None:
                raise ApplicationNotFoundError(clean_id)

            current_status = str(row.get("status") or "active")
            if current_status not in PENDING_STATUSES:
                return _conflict_response(
                    "memory_not_pending",
                    "Apenas memórias pendentes ou em quarentena podem ser aprovadas.",
                    details={"memory_id": clean_id, "status": current_status},
                )

            fact = str(row.get("fact") or "").strip()
            if not fact:
                return _conflict_response(
                    "memory_empty_text",
                    "Não é possível aprovar uma memória sem texto.",
                    details={"memory_id": clean_id},
                )

            explicit_target_id = (conflicting_memory_id or "").strip() or None
            target_id = explicit_target_id
            if target_id is None:
                targets = _latest_conflict_targets(conn, user_id=user_id, memory_ids=[clean_id])
                target_id = targets.get(clean_id)

            target_row: Mapping[str, Any] | None = None
            if target_id:
                if target_id == clean_id:
                    return _conflict_response(
                        "invalid_conflict_target",
                        "A memória conflitante não pode ser a própria memória em revisão.",
                        details={"memory_id": clean_id, "conflicting_memory_id": target_id},
                    )
                target_row = conn.execute(
                    select(agent_memories).where(
                        agent_memories.c.memory_id == target_id,
                        agent_memories.c.user_id == user_id,
                    )
                ).mappings().first()
                if target_row is None:
                    if explicit_target_id is not None:
                        return _conflict_response(
                            "invalid_conflict_target",
                            "A memória conflitante informada não foi encontrada.",
                            details={"memory_id": clean_id, "conflicting_memory_id": target_id},
                        )
                    target_id = None
                elif explicit_target_id is not None:
                    same_scope = _memory_scope_key(row) == _memory_scope_key(target_row)
                    has_conflict = _has_recorded_conflict_link(
                        conn,
                        user_id=user_id,
                        memory_id=clean_id,
                        target_id=target_id,
                    )
                    if not same_scope and not has_conflict:
                        return _conflict_response(
                            "invalid_conflict_target",
                            "A memória conflitante informada não pertence ao mesmo escopo nem possui conflito registrado com esta proposta.",
                            details={"memory_id": clean_id, "conflicting_memory_id": target_id},
                        )

            # Case 1: keep_old ("Ficar com a antiga")
            if resolution == "keep_old":
                conn.execute(
                    update(agent_memories)
                    .where(
                        agent_memories.c.memory_id == clean_id,
                        agent_memories.c.user_id == user_id,
                    )
                    .values(status="invalid", invalid_at=now, updated_at=now)
                )
                lifecycle.record_event(
                    conn,
                    memory_id=clean_id,
                    user_id=user_id,
                    event="rejected",
                    actor=actor,
                    old_text=fact,
                    related_id=target_id,
                    conversation_id=str(row["conversation_id"]) if row.get("conversation_id") else None,
                    created_at=now,
                )
                updated_row = conn.execute(
                    select(agent_memories).where(
                        agent_memories.c.memory_id == clean_id,
                        agent_memories.c.user_id == user_id,
                    )
                ).mappings().one()
                return JSONResponse(
                    {
                        "memory": _serialize_memory(updated_row),
                        "conflicting_memory": _serialize_memory(target_row) if target_row is not None else None,
                        "resolution": "keep_old",
                        "status": "invalid",
                    }
                )

            # Pre-validate keep_both BEFORE mutating either row so returning 409 commits nothing.
            if resolution == "keep_both" and target_id is not None and target_row is not None:
                target_status = str(target_row.get("status") or "")
                target_fact = str(target_row.get("fact") or "").strip()
                if target_status == "deleted" or not target_fact:
                    return _conflict_response(
                        "tombstone_text_erased",
                        "A memória conflitante foi esquecida e teve o texto apagado; não pode ser mantida ativa.",
                        details={
                            "memory_id": clean_id,
                            "conflicting_memory_id": target_id,
                            "status": target_status,
                        },
                    )
                row_hash = str(row.get("text_hash") or "").strip() or compute_text_hash(fact)
                target_hash = str(target_row.get("text_hash") or "").strip() or compute_text_hash(target_fact)
                if _memory_scope_key(row) == _memory_scope_key(target_row) and row_hash == target_hash:
                    return _conflict_response(
                        "duplicate_active_memory",
                        "As duas memórias têm texto idêntico no mesmo escopo e não podem coexistir ativas.",
                        details={"memory_id": clean_id, "existing_memory_id": target_id},
                    )
                if target_status != "active":
                    target_dup = _find_active_hash_duplicate(conn, target_row, exclude_memory_id=target_id)
                    if target_dup is not None:
                        return _conflict_response(
                            "duplicate_active_memory",
                            "Já existe uma memória ativa com texto idêntico neste escopo.",
                            details={
                                "memory_id": target_id,
                                "existing_memory_id": str(target_dup["memory_id"]),
                            },
                        )

            # Case 2: keep_new ("Ficar com a nova") — supersede conflicting old memory atomically
            superseded_id: str | None = None
            if resolution == "keep_new" and target_id is not None and target_row is not None:
                if str(target_row.get("status") or "") == "active":
                    if lifecycle.supersede(
                        conn,
                        old_id=target_id,
                        new_id=clean_id,
                        actor=actor,
                        new_text=fact,
                        user_id=user_id,
                        conversation_id=str(row["conversation_id"]) if row.get("conversation_id") else None,
                        now=now,
                    ):
                        superseded_id = target_id

            # Check for active duplicate with the same text_hash in this scope
            dup = _find_active_hash_duplicate(conn, row, exclude_memory_id=clean_id)
            if dup is not None:
                if resolution == "keep_new":
                    lifecycle.supersede(
                        conn,
                        old_id=str(dup["memory_id"]),
                        new_id=clean_id,
                        actor=actor,
                        new_text=fact,
                        user_id=user_id,
                        now=now,
                    )
                else:
                    return _conflict_response(
                        "duplicate_active_memory",
                        "Já existe uma memória ativa com texto idêntico neste escopo.",
                        details={"memory_id": clean_id, "existing_memory_id": str(dup["memory_id"])},
                    )

            conn.execute(
                update(agent_memories)
                .where(
                    agent_memories.c.memory_id == clean_id,
                    agent_memories.c.user_id == user_id,
                )
                .values(
                    status="active",
                    superseded_by=None,
                    invalid_at=None,
                    updated_at=now,
                )
            )
            lifecycle.record_event(
                conn,
                memory_id=clean_id,
                user_id=user_id,
                event="approved",
                actor=actor,
                new_text=fact,
                related_id=target_id,
                conversation_id=str(row["conversation_id"]) if row.get("conversation_id") else None,
                created_at=now,
            )

            # Case 3: keep_both ("Manter as duas") — ensure both active and audit both sides
            if resolution == "keep_both" and target_id is not None and target_row is not None:
                target_fact = str(target_row.get("fact") or "").strip()
                if str(target_row.get("status") or "") != "active" and target_fact:
                    conn.execute(
                        update(agent_memories)
                        .where(
                            agent_memories.c.memory_id == target_id,
                            agent_memories.c.user_id == user_id,
                        )
                        .values(status="active", superseded_by=None, invalid_at=None, updated_at=now)
                    )
                lifecycle.record_event(
                    conn,
                    memory_id=target_id,
                    user_id=user_id,
                    event="approved",
                    actor=actor,
                    new_text=target_fact or None,
                    related_id=clean_id,
                    conversation_id=str(target_row["conversation_id"]) if target_row.get("conversation_id") else None,
                    created_at=now,
                )

            updated_new = conn.execute(
                select(agent_memories).where(
                    agent_memories.c.memory_id == clean_id,
                    agent_memories.c.user_id == user_id,
                )
            ).mappings().one()
            updated_old = (
                conn.execute(
                    select(agent_memories).where(
                        agent_memories.c.memory_id == target_id,
                        agent_memories.c.user_id == user_id,
                    )
                ).mappings().first()
                if target_id
                else None
            )

            return JSONResponse(
                {
                    "memory": _serialize_memory(updated_new),
                    "conflicting_memory": _serialize_memory(updated_old) if updated_old is not None else None,
                    "resolution": resolution or "approve",
                    "status": "active",
                    "superseded_memory_id": superseded_id,
                }
            )
    except IntegrityError:
        return _conflict_response(
            "duplicate_active_memory",
            "Já existe uma memória ativa com texto idêntico neste escopo.",
            details={"memory_id": clean_id},
        )


def _reject_memory(engine: Engine, *, user_id: str, memory_id: str) -> JSONResponse:
    clean_id = str(memory_id or "").strip()
    if not clean_id:
        raise ApplicationNotFoundError(memory_id)

    lifecycle = MemoryLifecycle(engine)
    actor = f"user:{user_id}"
    now = datetime.now(UTC)

    with engine.begin() as conn:
        row = conn.execute(
            select(agent_memories).where(
                agent_memories.c.memory_id == clean_id,
                agent_memories.c.user_id == user_id,
            )
        ).mappings().first()
        if row is None:
            raise ApplicationNotFoundError(clean_id)

        if str(row.get("status") or "") == "deleted":
            return _conflict_response(
                "memory_already_deleted",
                "Memória já foi esquecida e não pode ser rejeitada.",
                details={"memory_id": clean_id},
            )

        conn.execute(
            update(agent_memories)
            .where(
                agent_memories.c.memory_id == clean_id,
                agent_memories.c.user_id == user_id,
            )
            .values(status="invalid", invalid_at=now, updated_at=now)
        )
        lifecycle.record_event(
            conn,
            memory_id=clean_id,
            user_id=user_id,
            event="rejected",
            actor=actor,
            old_text=str(row.get("fact") or "") or None,
            conversation_id=str(row["conversation_id"]) if row.get("conversation_id") else None,
            created_at=now,
        )
        updated = conn.execute(
            select(agent_memories).where(
                agent_memories.c.memory_id == clean_id,
                agent_memories.c.user_id == user_id,
            )
        ).mappings().one()

    return JSONResponse({"memory": _serialize_memory(updated), "status": "invalid"})


def _restore_memory(engine: Engine, *, user_id: str, memory_id: str) -> JSONResponse:
    clean_id = str(memory_id or "").strip()
    if not clean_id:
        raise ApplicationNotFoundError(memory_id)

    lifecycle = MemoryLifecycle(engine)
    actor = f"user:{user_id}"

    with engine.connect() as conn:
        row = conn.execute(
            select(agent_memories).where(
                agent_memories.c.memory_id == clean_id,
                agent_memories.c.user_id == user_id,
            )
        ).mappings().first()

    if row is None:
        raise ApplicationNotFoundError(clean_id)

    current_status = str(row.get("status") or "active")
    if current_status == "active":
        return _conflict_response(
            "memory_already_active",
            "Esta memória já está ativa.",
            details={"memory_id": clean_id, "status": current_status},
        )
    if current_status in PENDING_STATUSES:
        return _conflict_response(
            "memory_requires_approval",
            "Use aprovar para ativar uma memória pendente ou em quarentena.",
            details={"memory_id": clean_id, "status": current_status},
        )
    if not str(row.get("fact") or "").strip():
        return _conflict_response(
            "tombstone_text_erased",
            "Memória esquecida teve o texto apagado e não pode ser restaurada.",
            details={"memory_id": clean_id, "status": current_status},
        )

    try:
        outcome = lifecycle.restore(clean_id, user_id, actor=actor)
    except IntegrityError:
        return _conflict_response(
            "duplicate_active_memory",
            "Já existe uma memória ativa com o mesmo texto neste escopo.",
            details={"memory_id": clean_id},
        )

    if not outcome.get("ok"):
        error_code = str(outcome.get("error") or "restore_failed")
        message = str(outcome.get("message") or "Não foi possível restaurar esta memória.")
        if error_code == "memory_not_found":
            raise ApplicationNotFoundError(clean_id)
        return _conflict_response(error_code, message, details={"memory_id": clean_id})

    with engine.connect() as conn:
        restored = conn.execute(
            select(agent_memories).where(
                agent_memories.c.memory_id == clean_id,
                agent_memories.c.user_id == user_id,
            )
        ).mappings().one()

    return JSONResponse({"memory": _serialize_memory(restored), "status": "active"})


def _memory_history_payload(engine: Engine, *, user_id: str, memory_id: str) -> dict[str, Any]:
    clean_id = str(memory_id or "").strip()
    if not clean_id:
        raise ApplicationNotFoundError(memory_id)

    with engine.connect() as conn:
        row = conn.execute(
            select(agent_memories).where(
                agent_memories.c.memory_id == clean_id,
                agent_memories.c.user_id == user_id,
            )
        ).mappings().first()
    if row is None:
        raise ApplicationNotFoundError(clean_id)

    lifecycle = MemoryLifecycle(engine)
    raw_events = lifecycle.history(clean_id, user_id)
    last_forgotten_idx = -1
    last_restored_idx = -1
    for idx, ev in enumerate(raw_events):
        ev_name = str(ev.get("event") or "")
        if ev_name == "forgotten":
            last_forgotten_idx = idx
        elif ev_name == "restored":
            last_restored_idx = idx
    scrub_all = (
        str(row.get("status") or "") == "deleted"
        or (last_forgotten_idx >= 0 and last_restored_idx < last_forgotten_idx)
    )
    sanitized_events: list[dict[str, Any]] = []
    for idx, ev in enumerate(raw_events):
        scrub_event = scrub_all or idx <= last_forgotten_idx or str(ev.get("event") or "") == "forgotten"
        sanitized_events.append(
            {
                "id": int(ev["id"]),
                "memory_id": str(ev["memory_id"]),
                "event": str(ev["event"]),
                "old_text": None if scrub_event else ev.get("old_text"),
                "new_text": None if scrub_event else ev.get("new_text"),
                "related_id": ev.get("related_id"),
                "actor": str(ev.get("actor") or "system"),
                "conversation_id": ev.get("conversation_id"),
                "turn_id": ev.get("turn_id"),
                "created_at": ev.get("created_at"),
            }
        )

    return {
        "memory_id": clean_id,
        "memory": _serialize_memory(row),
        "events": sanitized_events,
    }


def register_memory_review_routes(
    app: FastAPI,
    services: Any,
    principal_for: Callable[..., AuthenticatedPrincipal],
) -> None:
    """Register `/v1/memory/review`, `/v1/memory/{id}/approve|resolve-conflict|reject|restore|history`."""

    @app.get("/v1/memory/review")
    async def list_memory_review(
        request: Request,
        project_id: str | None = None,
        agent_key: str | None = None,
        status: str | None = None,
        scope_type: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> JSONResponse:
        def _sync() -> dict[str, Any]:
            principal = principal_for(request, mutable=False)
            services.security.authorize(
                principal,
                action="memory.list",
                resource_id=project_id,
                purpose="memory.review",
            )
            engine = _resolve_engine(services)
            return _list_review_payload(
                engine,
                user_id=principal.user_id,
                project_id=project_id,
                agent_key=agent_key,
                status=status,
                scope_type=scope_type,
                query=query,
                limit=limit,
            )

        return JSONResponse(await run_in_threadpool(_sync))

    @app.post("/v1/memory/{memory_id}/approve")
    async def approve_memory(
        memory_id: str,
        request: Request,
        body: ApproveMemoryRequest | None = None,
    ) -> JSONResponse:
        req = body or ApproveMemoryRequest()

        def _sync() -> JSONResponse:
            principal = principal_for(request, mutable=True)
            services.security.check_rate_limit(
                principal,
                action="memory.write",
                origin=request.headers.get("origin"),
            )
            services.security.authorize(
                principal,
                action="memory.write",
                resource_id=memory_id,
                purpose="memory.approve",
            )
            engine = _resolve_engine(services)
            return _approve_or_resolve(
                engine,
                user_id=principal.user_id,
                memory_id=memory_id,
                resolution=req.resolution,
                conflicting_memory_id=req.conflicting_memory_id,
            )

        return await run_in_threadpool(_sync)

    @app.post("/v1/memory/{memory_id}/resolve-conflict")
    async def resolve_memory_conflict(
        memory_id: str,
        request: Request,
        body: ResolveConflictRequest,
    ) -> JSONResponse:
        def _sync() -> JSONResponse:
            principal = principal_for(request, mutable=True)
            services.security.check_rate_limit(
                principal,
                action="memory.write",
                origin=request.headers.get("origin"),
            )
            services.security.authorize(
                principal,
                action="memory.write",
                resource_id=memory_id,
                purpose="memory.resolve_conflict",
            )
            engine = _resolve_engine(services)
            return _approve_or_resolve(
                engine,
                user_id=principal.user_id,
                memory_id=memory_id,
                resolution=body.resolution,
                conflicting_memory_id=body.conflicting_memory_id,
            )

        return await run_in_threadpool(_sync)

    @app.post("/v1/memory/{memory_id}/reject")
    async def reject_memory(memory_id: str, request: Request) -> JSONResponse:
        def _sync() -> JSONResponse:
            principal = principal_for(request, mutable=True)
            services.security.check_rate_limit(
                principal,
                action="memory.write",
                origin=request.headers.get("origin"),
            )
            services.security.authorize(
                principal,
                action="memory.write",
                resource_id=memory_id,
                purpose="memory.reject",
            )
            engine = _resolve_engine(services)
            return _reject_memory(engine, user_id=principal.user_id, memory_id=memory_id)

        return await run_in_threadpool(_sync)

    @app.post("/v1/memory/{memory_id}/restore")
    async def restore_memory(memory_id: str, request: Request) -> JSONResponse:
        def _sync() -> JSONResponse:
            principal = principal_for(request, mutable=True)
            services.security.check_rate_limit(
                principal,
                action="memory.write",
                origin=request.headers.get("origin"),
            )
            services.security.authorize(
                principal,
                action="memory.write",
                resource_id=memory_id,
                purpose="memory.restore",
            )
            engine = _resolve_engine(services)
            return _restore_memory(engine, user_id=principal.user_id, memory_id=memory_id)

        return await run_in_threadpool(_sync)

    @app.get("/v1/memory/{memory_id}/history")
    async def get_memory_history(memory_id: str, request: Request) -> JSONResponse:
        def _sync() -> dict[str, Any]:
            principal = principal_for(request, mutable=False)
            services.security.authorize(
                principal,
                action="memory.list",
                resource_id=memory_id,
                purpose="memory.history",
            )
            engine = _resolve_engine(services)
            return _memory_history_payload(engine, user_id=principal.user_id, memory_id=memory_id)

        return JSONResponse(await run_in_threadpool(_sync))


__all__ = [
    "ApproveMemoryRequest",
    "ResolveConflictRequest",
    "register_memory_review_routes",
]
