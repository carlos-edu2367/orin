"""Durable store for the facts an agent chooses to remember.

Scope is the user or project, so a fact learned in one conversation is available
to the next one. ``agent_memories`` is the single source of truth; ranking uses
the derived ``MemoryHybridIndex`` (FTS5 BM25 + optional dense vectors fused with
Reciprocal Rank Fusion, recency, project affinity, usage and confidence).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
import re
from threading import Lock
from uuid import uuid4

from sqlalchemy import delete, inspect, insert, select, text, update
from sqlalchemy.engine import Engine

from agentos.agentic.learning import is_ephemeral_mechanical_fact
from agentos.memory.hybrid_index import MemoryHybridIndex
from agentos.memory.security import validate_memory_content
from agentos.retrieval.ports import EmbeddingPort

from .schema import agent_memories


_WORD = re.compile(r"[\wÀ-ÿ]{3,}", re.UNICODE)
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "was", "were", "are", "you", "your",
    "que", "com", "para", "uma", "dos", "das", "por", "sobre", "como", "meu", "minha", "sua", "seu",
})

FRIEND_MEMORY_KINDS = frozenset({"episodic", "inside_joke", "milestone", "emotional"})
_FRIEND_ONLY_KINDS = frozenset({"inside_joke", "milestone", "emotional"})
STANDARD_MEMORY_KINDS = frozenset({"fact", "preference", "architecture", "operational", "episodic"})
VALID_SCOPE_TYPES = frozenset({"user", "project", "friend"})
MAX_FACT_CHARS = 4_000
_NEAR_DUPLICATE_OVERLAP = 0.88

_PROMPT_INJECTION_RE = re.compile(
    r"(?ix)(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|policy|prompts|rules)|"
    r"<\s*/?\s*system\s*>|system\s+prompt|developer\s+message)"
)
_NEGATION_RE = re.compile(
    r"\b(?:"
    r"não|nao|nunca|jamais|evite|evitar|proibido|proibida|sem|"
    r"never|not|dont|don't|cannot|can't|won't|wont|shouldn't|shouldnt|mustn't|mustnt|"
    r"neither|nor|avoid|without|no\s+longer|no\s+more"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)
_NEG_CLAUSE_SPLIT_RE = re.compile(r"(?:[,;.]|\b(?:e|mas|porém|porem|and|but)\b)", re.IGNORECASE | re.UNICODE)
_NEG_TARGET_WORD_RE = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)
_NEG_FILLER_WORDS = frozenset({
    *_STOPWORDS,
    "de", "do", "da", "em", "no", "na", "nos", "nas", "ao", "aos",
    "um", "uma", "uns", "umas", "os", "as", "ou", "se", "neste", "nesta", "nesse", "nessa",
    "sempre", "always", "the", "an", "to", "in", "on", "of", "at", "by", "or", "is", "be",
})

MAX_THROTTLE_ENGINES = 64
MAX_THROTTLE_ENTRIES_PER_ENGINE = 4_096
_THROTTLE_LOCK = Lock()
_ENGINE_USAGE_THROTTLES: dict[int, set[tuple[str, str]]] = {}


def _terms(text_val: str) -> set[str]:
    return {word.lower() for word in _WORD.findall(text_val or "")} - _STOPWORDS


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _negated_targets(text_val: str) -> set[tuple[str, tuple[str, ...]]]:
    targets: set[tuple[str, tuple[str, ...]]] = set()
    raw = text_val or ""
    for match in _NEGATION_RE.finditer(raw):
        tail = raw[match.end() :]
        clause = _NEG_CLAUSE_SPLIT_RE.split(tail, maxsplit=1)[0]
        tokens = [
            tok.lower()
            for tok in _NEG_TARGET_WORD_RE.findall(clause)
            if tok.lower() not in _NEG_FILLER_WORDS
        ]
        targets.add(("neg", tuple(tokens[:3])))
    return targets


def _has_polarity_inversion(text_a: str, text_b: str) -> bool:
    neg_a = [m.group(0).lower() for m in _NEGATION_RE.finditer(text_a or "")]
    neg_b = [m.group(0).lower() for m in _NEGATION_RE.finditer(text_b or "")]
    if (bool(neg_a) != bool(neg_b)) or (len(neg_a) != len(neg_b)):
        return True
    if not neg_a and not neg_b:
        return False
    return _negated_targets(text_a) != _negated_targets(text_b)


def _merge_tags(existing: Sequence[object] | None, incoming: Sequence[object] | None) -> list[str]:
    prev = [str(t) for t in (existing or ()) if str(t).strip()]
    new = [str(t) for t in (incoming or ()) if str(t).strip()]
    if not prev:
        return new
    if not new:
        return prev
    return sorted(set(prev) | set(new))


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
        data_dir: Path | str | None = None,
        index_path: Path | str | None = None,
        embedder: EmbeddingPort | None = None,
        hybrid_index: MemoryHybridIndex | None = None,
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

        if hybrid_index is not None:
            self._hybrid_index = hybrid_index
        else:
            db_name = str(getattr(engine.url, "database", "") or "")
            resolved_index_path: Path | str | None = index_path
            if resolved_index_path is None and data_dir is None and db_name in {"", ":memory:"}:
                resolved_index_path = ":memory:"
            self._hybrid_index = MemoryHybridIndex(
                user_id,
                index_path=resolved_index_path,
                data_dir=data_dir,
                embedder=embedder,
            )

        with _THROTTLE_LOCK:
            if len(_ENGINE_USAGE_THROTTLES) >= MAX_THROTTLE_ENGINES and id(engine) not in _ENGINE_USAGE_THROTTLES:
                oldest_engine_id = next(iter(_ENGINE_USAGE_THROTTLES))
                _ENGINE_USAGE_THROTTLES.pop(oldest_engine_id, None)
            self._usage_throttle = _ENGINE_USAGE_THROTTLES.setdefault(id(engine), set())

    @property
    def hybrid_index(self) -> MemoryHybridIndex:
        return self._hybrid_index

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
        supersedes_id: str | None = None,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        normalized = " ".join(str(fact or "").split())[:MAX_FACT_CHARS]
        effective_scope = (
            scope_type
            if scope_type is not None
            else (
                self._explicit_scope_type
                if self._explicit_scope_type is not None
                else ("friend" if kind in _FRIEND_ONLY_KINDS else self._scope_type)
            )
        )
        if effective_scope not in VALID_SCOPE_TYPES:
            raise ValueError(f"unsupported memory scope_type: {effective_scope!r}")

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
        target_supersede = str(supersedes_id).strip() if supersedes_id and str(supersedes_id).strip() else None

        with self._engine.begin() as connection:
            existing_row = connection.execute(
                select(agent_memories).where(
                    agent_memories.c.user_id == self._user_id,
                    agent_memories.c.fact == normalized,
                    agent_memories.c.scope_type == effective_scope,
                    scope_predicate,
                )
            ).mappings().first()
            if existing_row is not None:
                existing_id = str(existing_row["memory_id"])
                merged_tags = _merge_tags(existing_row.get("tags"), tags)
                merged_conf = max(float(existing_row.get("confidence") or 0.0), float(confidence))
                connection.execute(
                    update(agent_memories)
                    .where(agent_memories.c.memory_id == existing_id)
                    .values(
                        updated_at=now,
                        tags=merged_tags,
                        kind=kind,
                        confidence=merged_conf,
                        source=source,
                        emotional_valence=valence,
                        salience=salience_val,
                        superseded_by=None,
                    )
                )
                superseded = (
                    self._supersede_by_id(connection, target_supersede, existing_id, now)
                    if target_supersede and target_supersede != existing_id
                    else []
                )
                return {"memory_id": existing_id, "fact": normalized, "created": False, "superseded": superseded}

            near_dup = self._find_near_duplicate(
                connection,
                normalized,
                kind,
                scope_type=effective_scope,
                project_id=effective_project_id,
                exclude_id=target_supersede,
            )
            if near_dup is not None:
                dup_id = str(near_dup["memory_id"])
                merged_tags = _merge_tags(near_dup.get("tags"), tags)
                merged_conf = max(float(near_dup.get("confidence") or 0.0), float(confidence))
                connection.execute(
                    update(agent_memories)
                    .where(agent_memories.c.memory_id == dup_id)
                    .values(
                        fact=normalized,
                        updated_at=now,
                        tags=merged_tags,
                        confidence=merged_conf,
                        source=source,
                        emotional_valence=valence,
                        salience=salience_val,
                    )
                )
                superseded = (
                    self._supersede_by_id(connection, target_supersede, dup_id, now)
                    if target_supersede and target_supersede != dup_id
                    else []
                )
                return {"memory_id": dup_id, "fact": normalized, "created": False, "superseded": superseded}

            memory_id = f"mem_{uuid4().hex}"
            superseded = (
                self._supersede_by_id(connection, target_supersede, memory_id, now)
                if target_supersede
                else []
            )
            connection.execute(
                insert(agent_memories).values(
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
                )
            )
        return {"memory_id": memory_id, "fact": normalized, "created": True, "superseded": superseded}

    def _find_near_duplicate(
        self,
        connection,
        fact: str,
        kind: str,
        *,
        scope_type: str,
        project_id: str | None,
        exclude_id: str | None = None,
    ) -> dict[str, object] | None:
        """Find an active memory in the same scope and kind with Jaccard >= 0.88 and same polarity."""
        wanted = _terms(fact)
        if not wanted:
            return None
        scope_predicate = (
            agent_memories.c.project_id == project_id
            if project_id
            else agent_memories.c.project_id.is_(None)
        )
        rows = connection.execute(
            select(agent_memories).where(
                agent_memories.c.user_id == self._user_id,
                agent_memories.c.scope_type == scope_type,
                agent_memories.c.kind == kind,
                agent_memories.c.superseded_by.is_(None),
                scope_predicate,
            )
        ).mappings().all()

        best_row: dict[str, object] | None = None
        best_jaccard = 0.0
        for row in rows:
            mid = str(row["memory_id"])
            if exclude_id and mid == exclude_id:
                continue
            existing_fact = str(row["fact"] or "")
            other = _terms(existing_fact)
            if not other:
                continue
            union_len = len(wanted | other)
            if union_len == 0:
                continue
            jaccard = len(wanted & other) / union_len
            if jaccard >= _NEAR_DUPLICATE_OVERLAP and not _has_polarity_inversion(fact, existing_fact):
                if jaccard > best_jaccard:
                    best_jaccard = jaccard
                    best_row = dict(row)
        return best_row

    def _supersede_by_id(
        self,
        connection,
        old_memory_id: str,
        replacement_id: str,
        now: datetime,
    ) -> list[str]:
        if not old_memory_id or old_memory_id == replacement_id:
            return []
        result = connection.execute(
            update(agent_memories)
            .where(
                agent_memories.c.memory_id == old_memory_id,
                agent_memories.c.user_id == self._user_id,
                agent_memories.c.superseded_by.is_(None),
            )
            .values(superseded_by=replacement_id, updated_at=now)
        )
        return [old_memory_id] if result.rowcount else []

    def supersede_memory(self, old_memory_id: str, new_memory_id: str) -> bool:
        """Explicitly chain ``old_memory_id`` to ``new_memory_id`` if both belong to this user."""
        old_id = str(old_memory_id or "").strip()
        new_id = str(new_memory_id or "").strip()
        if not old_id or not new_id or old_id == new_id:
            return False
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            new_exists = connection.execute(
                select(agent_memories.c.memory_id).where(
                    agent_memories.c.memory_id == new_id,
                    agent_memories.c.user_id == self._user_id,
                )
            ).scalar()
            if new_exists is None:
                return False
            replaced = self._supersede_by_id(connection, old_id, new_id, now)
        return bool(replaced)

    def get_memory(
        self,
        memory_id: str,
        *,
        conversation_id: str | None = None,
        record_usage: bool = True,
    ) -> dict[str, object] | None:
        """Fetch one memory by id strictly isolated to ``self._user_id``."""
        target_id = str(memory_id or "").strip()
        if not target_id:
            return None
        with self._engine.connect() as connection:
            row = connection.execute(
                select(agent_memories).where(
                    agent_memories.c.memory_id == target_id,
                    agent_memories.c.user_id == self._user_id,
                )
            ).mappings().first()
        if row is None:
            return None
        row_dict = dict(row)
        if record_usage:
            self._record_usage([row_dict], conversation_id=conversation_id)
        return self._public(row_dict)

    def _record_usage(
        self,
        rows: Sequence[dict[str, object]],
        *,
        conversation_id: str | None = None,
    ) -> None:
        if not rows:
            return
        effective_conv = (
            str(conversation_id).strip()
            if conversation_id is not None and str(conversation_id).strip()
            else (str(self._conversation_id).strip() if self._conversation_id and str(self._conversation_id).strip() else None)
        )
        to_update: list[dict[str, object]] = []
        with _THROTTLE_LOCK:
            for row in rows:
                mid = str(row.get("memory_id") or "")
                if not mid:
                    continue
                if effective_conv is not None:
                    key = (effective_conv, mid)
                    if key in self._usage_throttle:
                        continue
                    if len(self._usage_throttle) >= MAX_THROTTLE_ENTRIES_PER_ENGINE:
                        self._usage_throttle.clear()
                    self._usage_throttle.add(key)
                to_update.append(row)

        if not to_update:
            return
        now = datetime.now(UTC)
        ids = [str(r["memory_id"]) for r in to_update]
        with self._engine.begin() as connection:
            connection.execute(
                update(agent_memories)
                .where(
                    agent_memories.c.memory_id.in_(ids),
                    agent_memories.c.user_id == self._user_id,
                )
                .values(
                    hit_count=agent_memories.c.hit_count + 1,
                    last_used_at=now,
                )
            )
        for row in to_update:
            row["hit_count"] = int(row.get("hit_count") or 0) + 1
            row["last_used_at"] = now

    def relevant(
        self,
        task: str,
        *,
        limit: int = 12,
        conversation_id: str | None = None,
    ) -> list[dict[str, object]]:
        """Memories worth spending prompt space on for *this* task.

        Standing preferences get up to ``min(4, limit)`` guaranteed slots, and
        the remaining budget is filled by the hybrid index (BM25 + dense vectors
        fused via RRF, plus project affinity, recency, hit count and confidence).
        """
        if limit <= 0:
            return []
        now = datetime.now(UTC)
        preference_slots = min(4, limit)
        rows = [
            row
            for row in self._all()
            if row["superseded_by"] is None
            and not (row.get("source") == "mechanical" and is_ephemeral_mechanical_fact(str(row.get("fact") or "")))
        ]
        if not rows:
            return []

        final_scores, rrf_scores = self._hybrid_index.rank(
            task,
            rows,
            all_user_rows=self._all_active_user_rows(),
            active_project_id=self._project_id,
            now=now,
            min_vector_similarity=0.20,
        )

        preferences = sorted(
            (row for row in rows if row["kind"] == "preference"),
            key=lambda row: (
                final_scores.get(str(row["memory_id"]), 0.0),
                float(row["confidence"] or 0.0),
                row["updated_at"],
            ),
            reverse=True,
        )[:preference_slots]
        chosen = {str(row["memory_id"]): row for row in preferences}
        if self._project_id and limit >= 4 and len(chosen) < preference_slots:
            user_standing = sorted(
                (
                    row
                    for row in rows
                    if row.get("scope_type") == "user"
                    and row.get("source") != "mechanical"
                    and str(row["memory_id"]) not in chosen
                ),
                key=lambda row: (
                    final_scores.get(str(row["memory_id"]), 0.0),
                    float(row["confidence"] or 0.0),
                    row["updated_at"],
                ),
                reverse=True,
            )[: preference_slots - len(chosen)]
            for row in user_standing:
                chosen[str(row["memory_id"])] = row

        def _candidate_sort_key(row: dict[str, object]) -> tuple[int, float, int, object]:
            mid = str(row["memory_id"])
            matched = 1 if rrf_scores.get(mid, 0.0) > 0.0 else 0
            non_mechanical = 0 if row.get("source") == "mechanical" else 1
            return (
                matched,
                final_scores.get(mid, 0.0),
                non_mechanical,
                row["updated_at"],
            )

        for row in sorted(rows, key=_candidate_sort_key, reverse=True):
            if len(chosen) >= limit:
                break
            chosen.setdefault(str(row["memory_id"]), row)

        selected_rows = list(chosen.values())[:limit]
        self._record_usage(selected_rows, conversation_id=conversation_id)
        return [self._public(row) for row in selected_rows]

    def relevant_for_friend(
        self,
        task: str,
        *,
        limit: int = 18,
        conversation_id: str | None = None,
    ) -> list[dict[str, object]]:
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
            return self.relevant(task, limit=limit, conversation_id=conversation_id)
        if not layer1_rows:
            layer1_budget = 0
            layer2_budget = min(8, limit)
        elif limit >= 18:
            layer1_budget = min(10, len(layer1_rows))
            layer2_budget = min(8, limit - layer1_budget)
        else:
            layer2_budget = min(8, len(layer2_rows), max(1, (limit * 8) // 18 or 1))
            layer1_budget = max(0, min(10, limit - layer2_budget))

        layer1_selected = (
            self.relevant(task, limit=layer1_budget, conversation_id=conversation_id)
            if layer1_budget > 0
            else []
        )

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

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        scope: str | None = None,
        conversation_id: str | None = None,
    ) -> list[dict[str, object]]:
        if limit <= 0:
            return []
        all_rows = self._all()
        if scope is not None and str(scope).strip().lower() in {"user", "project"}:
            norm_scope = str(scope).strip().lower()
            rows = [r for r in all_rows if r.get("scope_type") == norm_scope]
        else:
            rows = all_rows
        if not rows:
            return []

        now = datetime.now(UTC)
        if not str(query or "").strip():
            selected = rows[:limit]
            self._record_usage(selected, conversation_id=conversation_id)
            return [self._public(row) for row in selected]

        final_scores, rrf_scores = self._hybrid_index.rank(
            query,
            rows,
            all_user_rows=self._all_active_user_rows(),
            active_project_id=self._project_id,
            now=now,
            min_vector_similarity=0.25,
        )
        matched_rows = [
            row for row in rows if rrf_scores.get(str(row["memory_id"]), 0.0) > 0.0
        ]
        if not matched_rows:
            return []
        matched_rows.sort(
            key=lambda row: (final_scores.get(str(row["memory_id"]), 0.0), row["updated_at"]),
            reverse=True,
        )
        selected = matched_rows[:limit]
        self._record_usage(selected, conversation_id=conversation_id)
        return [self._public(row) for row in selected]

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

    def close(self) -> None:
        self._hybrid_index.close()

    def _all_active_user_rows(self) -> list[dict[str, object]]:
        predicate = [
            agent_memories.c.user_id == self._user_id,
            agent_memories.c.superseded_by.is_(None),
        ]
        with self._engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(agent_memories)
                    .where(*predicate)
                    .order_by(agent_memories.c.updated_at.desc())
                    .limit(1000)
                )
                .mappings()
                .all()
            ]

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
        updated = row.get("updated_at")
        last_used = row.get("last_used_at")
        return {
            "memory_id": row["memory_id"],
            "fact": row["fact"],
            "tags": list(row["tags"] or []),
            "kind": row["kind"],
            "scope_type": row.get("scope_type", "user"),
            "project_id": row.get("project_id"),
            "confidence": float(row["confidence"] or 0.0),
            "emotional_valence": float(row.get("emotional_valence") or 0.0),
            "salience": float(row.get("salience") if row.get("salience") is not None else 0.5),
            "hit_count": int(row.get("hit_count") or 0),
            "last_used_at": last_used.isoformat() if isinstance(last_used, datetime) else last_used,
            "source": row["source"],
            "updated_at": updated.isoformat() if isinstance(updated, datetime) else updated,
        }


__all__ = [
    "FRIEND_MEMORY_KINDS",
    "MAX_FACT_CHARS",
    "MAX_THROTTLE_ENGINES",
    "MAX_THROTTLE_ENTRIES_PER_ENGINE",
    "PostgresAgentMemoryStore",
    "STANDARD_MEMORY_KINDS",
    "VALID_SCOPE_TYPES",
    "_has_polarity_inversion",
]
