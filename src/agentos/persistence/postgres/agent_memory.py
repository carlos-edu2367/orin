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

from sqlalchemy import delete, inspect, insert, select, text, update
from sqlalchemy.engine import Engine

from agentos.memory.security import validate_memory_content

from .schema import agent_memories


_WORD = re.compile(r"[\wÀ-ÿ]{3,}", re.UNICODE)
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "was", "were", "are", "you", "your",
    "que", "com", "para", "uma", "dos", "das", "por", "sobre", "como", "meu", "minha", "sua", "seu",
})

FRIEND_MEMORY_KINDS = frozenset({"episodic", "inside_joke", "milestone", "emotional"})
VALID_SCOPE_TYPES = frozenset({"user", "project", "friend"})
_PROMPT_INJECTION_RE = re.compile(
    r"(?ix)(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|policy|prompts|rules)|"
    r"<\s*/?\s*system\s*>|system\s+prompt|developer\s+message)"
)


def _terms(text_val: str) -> set[str]:
    return {word.lower() for word in _WORD.findall(text_val or "")} - _STOPWORDS


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


# How much of two facts' vocabulary must agree before the newer one is taken
# to replace the older. "o gerenciador de pacotes e npm" vs "...e pnpm" share
# 2 of their 4 distinct words (0.5) -- the differing word is expected to be
# exactly the lesson -- while two unrelated notes about the same project stay
# below this.
_CONTRADICTION_OVERLAP = 0.5


def _ensure_friend_columns(engine: Engine) -> None:
    try:
        inspector = inspect(engine)
        if "agent_memories" not in set(inspector.get_table_names()):
            return
        cols = {c["name"] for c in inspector.get_columns("agent_memories")}
        with engine.begin() as conn:
            if "emotional_valence" not in cols:
                conn.execute(text("ALTER TABLE agent_memories ADD COLUMN emotional_valence FLOAT NOT NULL DEFAULT 0.0"))
            if "salience" not in cols:
                conn.execute(text("ALTER TABLE agent_memories ADD COLUMN salience FLOAT NOT NULL DEFAULT 0.5"))
    except Exception:
        pass


class PostgresAgentMemoryStore:
    def __init__(
        self,
        engine: Engine,
        user_id: str,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
        execution_id: str | None = None,
        scope_type: str | None = None,
    ) -> None:
        self._engine = engine
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._project_id = project_id
        self._execution_id = execution_id
        self._explicit_scope_type = scope_type
        self._friend_turn_seq = 0
        self._recent_joke_turns: dict[str, int] = {}
        _ensure_friend_columns(engine)

    @property
    def _scope_type(self) -> str:
        if self._explicit_scope_type:
            return self._explicit_scope_type
        return "project" if self._project_id else "user"

    @property
    def _scope_id(self) -> str:
        if self._scope_type == "friend":
            return self._user_id
        return self._project_id or self._user_id

    def save(
        self,
        fact: str,
        tags: tuple[str, ...] | list[str] = (),
        *,
        kind: str = "fact",
        confidence: float = 1.0,
        source: str = "user_explicit",
        scope_type: str | None = None,
        emotional_valence: float = 0.0,
        salience: float = 0.5,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        normalized = " ".join(str(fact or "").split())
        effective_scope = (
            scope_type
            if scope_type is not None
            else (
                self._explicit_scope_type
                if self._explicit_scope_type is not None
                else ("friend" if kind in FRIEND_MEMORY_KINDS else self._scope_type)
            )
        )
        if effective_scope not in VALID_SCOPE_TYPES:
            raise ValueError(f"unsupported memory scope_type: {effective_scope!r}")

        if effective_scope == "friend" or kind in FRIEND_MEMORY_KINDS:
            validate_memory_content(normalized)
            if _PROMPT_INJECTION_RE.search(normalized):
                raise ValueError("content violates the memory data policy")

        valence = _clamp(emotional_valence, -1.0, 1.0)
        salience_val = _clamp(salience, 0.0, 1.0)
        effective_project_id = self._project_id if effective_scope == "project" else None
        effective_scope_id = effective_project_id or self._user_id
        scope_predicate = (
            agent_memories.c.project_id == effective_project_id
            if effective_project_id
            else agent_memories.c.project_id.is_(None)
        )
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(agent_memories.c.memory_id).where(
                    agent_memories.c.user_id == self._user_id,
                    agent_memories.c.fact == normalized,
                    agent_memories.c.scope_type == effective_scope,
                    scope_predicate,
                )
            ).scalar()
            if existing is not None:
                connection.execute(update(agent_memories).where(agent_memories.c.memory_id == existing).values(
                    updated_at=now,
                    tags=list(tags),
                    kind=kind,
                    confidence=confidence,
                    source=source,
                    emotional_valence=valence,
                    salience=salience_val,
                ))
                return {"memory_id": existing, "fact": normalized, "created": False, "superseded": []}
            memory_id = f"mem_{uuid4().hex}"
            superseded = self._supersede(
                connection,
                normalized,
                kind,
                memory_id,
                now,
                scope_type=effective_scope,
                project_id=effective_project_id,
            )
            connection.execute(insert(agent_memories).values(
                memory_id=memory_id,
                user_id=self._user_id,
                conversation_id=self._conversation_id,
                scope_type=effective_scope,
                scope_id=effective_scope_id,
                project_id=effective_project_id,
                source_message_id=None,
                source_execution_id=self._execution_id,
                fact=normalized,
                tags=list(tags),
                kind=kind,
                confidence=confidence,
                emotional_valence=valence,
                salience=salience_val,
                source=source,
                hit_count=0,
                last_used_at=None,
                superseded_by=None,
                created_at=now,
                updated_at=now,
            ))
        return {"memory_id": memory_id, "fact": normalized, "created": True, "superseded": superseded}

    def _supersede(
        self,
        connection,
        fact: str,
        kind: str,
        replacement: str,
        now: datetime,
        *,
        scope_type: str | None = None,
        project_id: str | None = None,
    ) -> list[str]:
        """Chain a contradicted memory to its replacement rather than deleting it.

        Two facts contradict when they are the same kind, in the same scope,
        and share most of their vocabulary: "o gerenciador é npm" against "o
        gerenciador é pnpm". Keeping both would put two mutually exclusive
        truths in the same prompt.
        """
        wanted = _terms(fact)
        if not wanted:
            return []
        effective_scope = scope_type or self._scope_type
        effective_project_id = self._project_id if project_id is None and effective_scope == "project" else project_id
        scope_predicate = (
            agent_memories.c.project_id == effective_project_id
            if effective_project_id
            else agent_memories.c.project_id.is_(None)
        )
        rows = connection.execute(select(agent_memories).where(
            agent_memories.c.user_id == self._user_id,
            agent_memories.c.scope_type == effective_scope,
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
        if limit <= 0:
            return []
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

    def relevant_for_friend(self, task: str, *, limit: int = 18) -> list[dict[str, object]]:
        """Dual-Layer retrieval for Orin Friend.

        Combines up to 10 memories from Layer 1 (`scope_type in {'user', 'project'}`)
        and up to 8 memories from Layer 2 (`scope_type == 'friend'`), applying a
        cooldown penalty to `inside_joke` memories used within the last 2 turns
        or with disproportionate `hit_count`.
        """
        if limit <= 0:
            return []
        self._friend_turn_seq += 1
        now = datetime.now(UTC)

        layer1_rows = [row for row in self._all() if row["superseded_by"] is None]
        layer2_rows = [row for row in self._all_friend() if row["superseded_by"] is None]

        if not layer2_rows:
            return self.relevant(task, limit=limit)
        if not layer1_rows:
            layer1_budget = 0
            layer2_budget = min(8, limit)
        elif limit >= 18:
            layer1_budget = min(10, len(layer1_rows))
            layer2_budget = min(8, limit - layer1_budget)
        else:
            layer2_budget = min(8, len(layer2_rows), max(1, (limit * 8) // 18 or 1))
            layer1_budget = max(0, min(10, limit - layer2_budget))

        layer1_selected = self.relevant(task, limit=layer1_budget) if layer1_budget > 0 else []

        wanted = _terms(task)
        joke_rows = [r for r in layer2_rows if r["kind"] == "inside_joke"]
        min_joke_hits = min((int(r.get("hit_count") or 0) for r in joke_rows), default=0)

        def _friend_score(row: dict[str, object]) -> tuple[int, int, float, float, object]:
            haystack = _terms(str(row["fact"])) | {str(tag).lower() for tag in (row["tags"] or [])}
            overlap = len(wanted & haystack)
            salience = float(row.get("salience") if row.get("salience") is not None else 0.5)
            confidence = float(row.get("confidence") or 0.0)
            mem_id = str(row["memory_id"])
            hit_count = int(row.get("hit_count") or 0)

            cooldown_penalty = 0
            effective_salience = salience
            if row["kind"] == "inside_joke":
                last_turn = self._recent_joke_turns.get(mem_id)
                used_in_last_2_turns = last_turn is not None and (self._friend_turn_seq - last_turn) <= 2
                disproportionate_hits = hit_count > min_joke_hits
                if used_in_last_2_turns or disproportionate_hits:
                    cooldown_penalty = 1 + (2 if used_in_last_2_turns else 0) + (hit_count - min_joke_hits)
                    effective_salience = salience - 0.35 * cooldown_penalty

            return (
                overlap,
                -cooldown_penalty,
                round(effective_salience, 4),
                confidence,
                row["updated_at"],
            )

        sorted_layer2 = sorted(layer2_rows, key=_friend_score, reverse=True)
        remaining_slots = max(layer2_budget, min(8, limit - len(layer1_selected)))
        chosen_layer2 = sorted_layer2[:remaining_slots]

        used_joke_ids = [str(r["memory_id"]) for r in chosen_layer2 if r["kind"] == "inside_joke"]
        if used_joke_ids:
            for jid in used_joke_ids:
                self._recent_joke_turns[jid] = self._friend_turn_seq
            with self._engine.begin() as connection:
                for jid in used_joke_ids:
                    connection.execute(
                        update(agent_memories)
                        .where(agent_memories.c.memory_id == jid)
                        .values(
                            hit_count=agent_memories.c.hit_count + 1,
                            last_used_at=now,
                        )
                    )

        combined = list(layer1_selected) + [self._public(row) for row in chosen_layer2]
        return combined[:limit]

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

    def list_friend_memories(self, *, limit: int = 50) -> list[dict[str, object]]:
        return [self._public(row) for row in self._all_friend()[:limit]]

    def forget(self, memory_id: str) -> bool:
        predicate = [
            agent_memories.c.memory_id == memory_id,
            agent_memories.c.user_id == self._user_id,
        ]
        if self._project_id:
            predicate.extend((
                agent_memories.c.scope_type == "project",
                agent_memories.c.project_id == self._project_id,
            ))
        else:
            predicate.extend((
                agent_memories.c.scope_type.in_(("user", "friend")),
                agent_memories.c.project_id.is_(None),
            ))
        with self._engine.begin() as connection:
            result = connection.execute(delete(agent_memories).where(*predicate))
        return bool(result.rowcount)

    def _all(self) -> list[dict[str, object]]:
        predicate = [agent_memories.c.user_id == self._user_id, agent_memories.c.superseded_by.is_(None)]
        if self._project_id:
            predicate.append(
                (agent_memories.c.scope_type == "user")
                | ((agent_memories.c.scope_type == "project") & (agent_memories.c.project_id == self._project_id))
            )
        else:
            predicate.extend((agent_memories.c.scope_type == "user", agent_memories.c.project_id.is_(None)))
        with self._engine.connect() as connection:
            return [dict(row) for row in connection.execute(
                select(agent_memories)
                .where(*predicate)
                .order_by(agent_memories.c.updated_at.desc())
                .limit(500)
            ).mappings().all()]

    def _all_friend(self) -> list[dict[str, object]]:
        predicate = [
            agent_memories.c.user_id == self._user_id,
            agent_memories.c.scope_type == "friend",
            agent_memories.c.superseded_by.is_(None),
        ]
        with self._engine.connect() as connection:
            return [dict(row) for row in connection.execute(
                select(agent_memories)
                .where(*predicate)
                .order_by(agent_memories.c.updated_at.desc())
                .limit(300)
            ).mappings().all()]

    @staticmethod
    def _public(row: dict[str, object]) -> dict[str, object]:
        return {
            "memory_id": row["memory_id"],
            "fact": row["fact"],
            "tags": list(row["tags"] or []),
            "kind": row["kind"],
            "scope_type": row.get("scope_type", "user"),
            "confidence": float(row["confidence"] or 0.0),
            "emotional_valence": float(row.get("emotional_valence") or 0.0),
            "salience": float(row.get("salience") if row.get("salience") is not None else 0.5),
            "hit_count": int(row.get("hit_count") or 0),
            "source": row["source"],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }


__all__ = ["FRIEND_MEMORY_KINDS", "PostgresAgentMemoryStore", "VALID_SCOPE_TYPES"]
