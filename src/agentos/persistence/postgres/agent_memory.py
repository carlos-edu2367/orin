"""Durable store for the facts an agent chooses to remember.

Scope is the user, so a fact learned in one conversation is available to the
next one. Retrieval is a bounded relevance scan rather than an embedding search:
a personal installation holds hundreds of facts, not millions, and a scan keeps
recall explainable and dependency-free.
"""
from __future__ import annotations

from datetime import UTC, datetime
import re
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from .schema import agent_memories


_WORD = re.compile(r"[\wÀ-ÿ]{3,}", re.UNICODE)
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "was", "were", "are", "you", "your",
    "que", "com", "para", "uma", "dos", "das", "por", "sobre", "como", "meu", "minha", "sua", "seu",
})


def _terms(text: str) -> set[str]:
    return {word.lower() for word in _WORD.findall(text or "")} - _STOPWORDS


# How much of two facts' vocabulary must agree before the newer one is taken
# to replace the older. "o gerenciador de pacotes e npm" vs "...e pnpm" share
# 2 of their 4 distinct words (0.5) -- the differing word is expected to be
# exactly the lesson -- while two unrelated notes about the same project stay
# below this.
_CONTRADICTION_OVERLAP = 0.5


class PostgresAgentMemoryStore:
    def __init__(self, engine: Engine, user_id: str, *, conversation_id: str | None = None, project_id: str | None = None, execution_id: str | None = None) -> None:
        self._engine = engine
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._project_id = project_id
        self._execution_id = execution_id

    @property
    def _scope_type(self) -> str:
        return "project" if self._project_id else "user"

    @property
    def _scope_id(self) -> str:
        return self._project_id or self._user_id

    def save(
        self,
        fact: str,
        tags: tuple[str, ...] = (),
        *,
        kind: str = "fact",
        confidence: float = 1.0,
        source: str = "user_explicit",
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        normalized = " ".join(fact.split())
        scope_predicate = (
            agent_memories.c.project_id == self._project_id
            if self._project_id
            else agent_memories.c.project_id.is_(None)
        )
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(agent_memories.c.memory_id).where(
                    agent_memories.c.user_id == self._user_id,
                    agent_memories.c.fact == normalized,
                    agent_memories.c.scope_type == self._scope_type,
                    scope_predicate,
                )
            ).scalar()
            if existing is not None:
                connection.execute(update(agent_memories).where(agent_memories.c.memory_id == existing).values(
                    updated_at=now, tags=list(tags), kind=kind, confidence=confidence, source=source,
                ))
                return {"memory_id": existing, "fact": normalized, "created": False, "superseded": []}
            memory_id = f"mem_{uuid4().hex}"
            superseded = self._supersede(connection, normalized, kind, memory_id, now)
            connection.execute(insert(agent_memories).values(
                memory_id=memory_id, user_id=self._user_id, conversation_id=self._conversation_id,
                scope_type=self._scope_type, scope_id=self._scope_id, project_id=self._project_id,
                source_message_id=None, source_execution_id=self._execution_id,
                fact=normalized, tags=list(tags), kind=kind, confidence=confidence, source=source,
                hit_count=0, last_used_at=None, superseded_by=None,
                created_at=now, updated_at=now,
            ))
        return {"memory_id": memory_id, "fact": normalized, "created": True, "superseded": superseded}

    def _supersede(self, connection, fact: str, kind: str, replacement: str, now: datetime) -> list[str]:
        """Chain a contradicted memory to its replacement rather than deleting it.

        Two facts contradict when they are the same kind, in the same scope,
        and share most of their vocabulary: "o gerenciador é npm" against "o
        gerenciador é pnpm". Keeping both would put two mutually exclusive
        truths in the same prompt.
        """
        wanted = _terms(fact)
        if not wanted:
            return []
        scope_predicate = (
            agent_memories.c.project_id == self._project_id
            if self._project_id
            else agent_memories.c.project_id.is_(None)
        )
        rows = connection.execute(select(agent_memories).where(
            agent_memories.c.user_id == self._user_id,
            agent_memories.c.scope_type == self._scope_type,
            agent_memories.c.kind == kind,
            agent_memories.c.superseded_by.is_(None),
            scope_predicate,
        )).mappings().all()
        replaced: list[str] = []
        for row in rows:
            other = _terms(str(row["fact"]))
            if not other:
                continue
            if len(wanted & other) / len(wanted | other) >= _CONTRADICTION_OVERLAP:
                replaced.append(str(row["memory_id"]))
        if replaced:
            connection.execute(update(agent_memories).where(
                agent_memories.c.memory_id.in_(replaced)
            ).values(superseded_by=replacement, updated_at=now))
        return replaced

    def relevant(self, task: str, *, limit: int = 12) -> list[dict[str, object]]:
        """Memories worth spending prompt space on for *this* task.

        Recency alone made the thirteenth memory permanently invisible and
        chose nothing for being useful. The budget is split instead: a fixed
        share for the strongest standing preferences, which apply to every
        turn regardless of subject, and the rest by how well a memory matches
        the task at hand. Project scope breaks a tie against global scope,
        because the more specific fact is the one that was learned here.
        """
        preference_slots = min(4, limit)
        rows = [row for row in self._all() if row["superseded_by"] is None]
        preferences = sorted(
            (row for row in rows if row["kind"] == "preference"),
            key=lambda row: (float(row["confidence"] or 0.0), row["updated_at"]),
            reverse=True,
        )[:preference_slots]
        chosen = {str(row["memory_id"]): row for row in preferences}

        wanted = _terms(task)

        def score(row) -> tuple[int, int, float]:
            haystack = _terms(str(row["fact"])) | {str(tag).lower() for tag in (row["tags"] or [])}
            return (
                len(wanted & haystack),
                1 if row["scope_type"] == "project" else 0,
                float(row["confidence"] or 0.0),
            )

        for row in sorted(rows, key=score, reverse=True):
            if len(chosen) >= limit:
                break
            chosen.setdefault(str(row["memory_id"]), row)
        return [self._public(row) for row in chosen.values()]

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, object]]:
        wanted = _terms(query)
        rows = self._all()
        if not wanted:
            return [self._public(row) for row in rows[:limit]]
        scored: list[tuple[int, dict[str, object]]] = []
        for row in rows:
            haystack = _terms(str(row["fact"])) | {str(tag).lower() for tag in (row["tags"] or [])}
            overlap = len(wanted & haystack)
            if overlap:
                scored.append((overlap, self._public(row)))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    def recent(self, *, limit: int = 12) -> list[dict[str, object]]:
        return [self._public(row) for row in self._all()[:limit]]

    def forget(self, memory_id: str) -> bool:
        predicate = [agent_memories.c.memory_id == memory_id, agent_memories.c.user_id == self._user_id, agent_memories.c.scope_type == self._scope_type]
        predicate.append(agent_memories.c.project_id == self._project_id if self._project_id else agent_memories.c.project_id.is_(None))
        with self._engine.begin() as connection:
            result = connection.execute(delete(agent_memories).where(*predicate))
        return bool(result.rowcount)

    def _all(self) -> list[dict[str, object]]:
        predicate = [agent_memories.c.user_id == self._user_id, agent_memories.c.superseded_by.is_(None)]
        if self._project_id:
            predicate.append((agent_memories.c.scope_type == "user") | ((agent_memories.c.scope_type == "project") & (agent_memories.c.project_id == self._project_id)))
        else:
            predicate.extend((agent_memories.c.scope_type == "user", agent_memories.c.project_id.is_(None)))
        with self._engine.connect() as connection:
            return [dict(row) for row in connection.execute(
                select(agent_memories)
                .where(*predicate)
                .order_by(agent_memories.c.updated_at.desc())
                .limit(500)
            ).mappings().all()]

    @staticmethod
    def _public(row: dict[str, object]) -> dict[str, object]:
        return {
            "memory_id": row["memory_id"],
            "fact": row["fact"],
            "tags": list(row["tags"] or []),
            "kind": row["kind"],
            "confidence": float(row["confidence"] or 0.0),
            "source": row["source"],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }


__all__ = ["PostgresAgentMemoryStore"]
