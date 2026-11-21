"""Durable store for the facts an agent chooses to remember.

Scope is the user or project, so a fact learned in one conversation is available
to the next one. ``agent_memories`` is the single source of truth; ranking uses
the derived ``MemoryHybridIndex`` (FTS5 BM25 + optional dense vectors fused with
Reciprocal Rank Fusion, recency, project affinity, usage and confidence).
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
import logging
from pathlib import Path
import re
from threading import Lock
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect, insert, select, text, update
from sqlalchemy.engine import Engine

from agentos.agentic.learning import is_ephemeral_mechanical_fact
from agentos.memory.agent_scope import AgentMemoryScope, is_learn_from_verified_only
from agentos.memory.conflict import (
    ConflictResolver,
    ExistingMemoryCandidate,
    MAX_CONFLICT_CANDIDATES,
    _has_polarity_inversion,
    _is_short_slot_replacement,
    compute_jaccard,
)
from agentos.memory.hybrid_index import MemoryHybridIndex
from agentos.memory.models_write import (
    MemoryWriteCandidate,
    MemoryWriteContext,
    WriteDecision,
    compute_text_hash,
    source_kind_from_legacy_source,
)
from agentos.memory.policy import WritePolicy, default_write_policy
from agentos.memory.security import validate_memory_content
from agentos.retrieval.ports import EmbeddingPort

from .memory_lifecycle import MemoryLifecycle
from .schema import agent_memories, memory_events

logger = logging.getLogger(__name__)


_WORD = re.compile(r"[\wÀ-ÿ]{3,}", re.UNICODE)
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "was", "were", "are", "you", "your",
    "que", "com", "para", "uma", "dos", "das", "por", "sobre", "como", "meu", "minha", "sua", "seu",
})

FRIEND_MEMORY_KINDS = frozenset({"episodic", "inside_joke", "milestone", "emotional"})
_FRIEND_ONLY_KINDS = frozenset({"inside_joke", "milestone", "emotional"})
STANDARD_MEMORY_KINDS = frozenset({"fact", "preference", "architecture", "operational", "episodic"})
VALID_SCOPE_TYPES = frozenset({"user", "project", "friend", "agent"})
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
        tables = set(inspector.get_table_names())
        if "agent_memories" not in tables:
            return
        cols = {c["name"] for c in inspector.get_columns("agent_memories")}
        ddl_by_col = (
            ("emotional_valence", "ALTER TABLE agent_memories ADD COLUMN emotional_valence FLOAT NOT NULL DEFAULT 0.0"),
            ("salience", "ALTER TABLE agent_memories ADD COLUMN salience FLOAT NOT NULL DEFAULT 0.5"),
            ("status", "ALTER TABLE agent_memories ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'active'"),
            ("agent_key", "ALTER TABLE agent_memories ADD COLUMN agent_key VARCHAR(64)"),
            ("text_hash", "ALTER TABLE agent_memories ADD COLUMN text_hash VARCHAR(64) NOT NULL DEFAULT ''"),
            ("valid_from", "ALTER TABLE agent_memories ADD COLUMN valid_from DATETIME"),
            ("invalid_at", "ALTER TABLE agent_memories ADD COLUMN invalid_at DATETIME"),
            ("source_kind", "ALTER TABLE agent_memories ADD COLUMN source_kind VARCHAR(16) NOT NULL DEFAULT 'agent'"),
            ("source_ref", "ALTER TABLE agent_memories ADD COLUMN source_ref VARCHAR(512)"),
            ("trust", "ALTER TABLE agent_memories ADD COLUMN trust SMALLINT NOT NULL DEFAULT 1"),
        )
        missing_ddl = [ddl for col_name, ddl in ddl_by_col if col_name not in cols]
        if missing_ddl:
            with engine.begin() as conn:
                for ddl in missing_ddl:
                    conn.execute(text(ddl))
        if "memory_events" not in tables:
            memory_events.create(engine, checkfirst=True)
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
        agent_key: str | None = None,
        agent_scope: AgentMemoryScope | None = None,
        scope_id: str | None = None,
        write_policy: WritePolicy | None = None,
        settings_provider: Callable[[], Any] | None = None,
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
        self._agent_scope = agent_scope
        self._explicit_scope_id = str(scope_id).strip() if scope_id and str(scope_id).strip() else None
        self._agent_key = (
            agent_scope.agent_key
            if agent_scope is not None
            else (str(agent_key).strip() if agent_key and str(agent_key).strip() else None)
        )
        self._settings_provider: Callable[[], Any] | None = settings_provider
        self._write_policy: WritePolicy = (
            write_policy
            if write_policy is not None
            else default_write_policy(settings_provider=self._resolve_settings)
        )
        self._lifecycle = MemoryLifecycle(engine)
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
    def lifecycle(self) -> MemoryLifecycle:
        return self._lifecycle

    def set_settings_provider(self, provider: Callable[[], Any] | None) -> None:
        """Bind a runtime settings provider (e.g. session EffectiveSettings) for policies like QuarantinePolicy."""
        self._settings_provider = provider

    def _resolve_settings(self) -> Any:
        return self._settings_provider() if callable(self._settings_provider) else None

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
        context: MemoryWriteContext | None = None,
        agent_key: str | None = None,
        agent_scope: AgentMemoryScope | None = None,
        scope_id: str | None = None,
        quarantine_enabled: bool | None = None,
        learn_from_verified_only: bool | None = None,
        propose_only: bool = False,
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
        active_agent_scope = agent_scope or (self._agent_scope if effective_scope == "agent" else None)
        resolved_agent_key = (
            (active_agent_scope.agent_key if active_agent_scope is not None else None)
            or (context.agent_key.strip() if context and context.agent_key and context.agent_key.strip() else None)
            or (agent_key.strip() if agent_key and agent_key.strip() else None)
            or (self._agent_key if effective_scope == "agent" else None)
        )
        if effective_scope == "agent":
            if not resolved_agent_key:
                raise ValueError("agent_key is required when scope_type='agent'")
            resolved_agent_key = resolved_agent_key.lower()
            if active_agent_scope is not None:
                effective_project_id = active_agent_scope.effective_project_id
                effective_scope_id = active_agent_scope.scope_id
            else:
                effective_project_id = self._project_id
                explicit_sid = (
                    str(scope_id).strip()
                    if scope_id and str(scope_id).strip()
                    else self._explicit_scope_id
                )
                effective_scope_id = explicit_sid or (
                    f"project:{self._project_id}:{resolved_agent_key}"
                    if self._project_id
                    else f"user:{resolved_agent_key}"
                )
        else:
            effective_project_id = self._project_id if effective_scope == "project" else None
            effective_scope_id = effective_project_id or self._user_id
        if context is not None:
            effective_context = MemoryWriteContext(
                source_kind=context.source_kind,
                source_ref=context.source_ref if context.source_ref is not None else self._execution_id,
                actor=context.actor or "system",
                agent_key=resolved_agent_key,
                conversation_id=context.conversation_id if context.conversation_id is not None else self._conversation_id,
                turn_id=context.turn_id,
                trust=int(context.trust),
                existing_candidates=context.existing_candidates,
                llm_adjudication=context.llm_adjudication,
            )
        else:
            inferred_kind, inferred_trust = source_kind_from_legacy_source(source)
            inferred_actor = (
                "user"
                if inferred_kind == "user"
                else (
                    "consolidator"
                    if inferred_kind == "consolidator"
                    else (f"agent:{resolved_agent_key}" if resolved_agent_key else "system")
                )
            )
            effective_context = MemoryWriteContext(
                source_kind=inferred_kind,
                source_ref=self._execution_id,
                actor=inferred_actor,
                agent_key=resolved_agent_key,
                conversation_id=self._conversation_id,
                turn_id=None,
                trust=inferred_trust,
            )

        text_hash = compute_text_hash(normalized)
        target_supersede = str(supersedes_id).strip() if supersedes_id and str(supersedes_id).strip() else None

        conflict_candidates = (
            effective_context.existing_candidates
            if effective_context.existing_candidates
            else self._find_conflict_candidates(
                normalized,
                kind,
                scope_type=effective_scope,
                project_id=effective_project_id,
                agent_key=resolved_agent_key,
                scope_id=effective_scope_id if effective_scope == "agent" else None,
                text_hash=text_hash,
                limit=MAX_CONFLICT_CANDIDATES,
            )
        )
        policy_context = MemoryWriteContext(
            source_kind=effective_context.source_kind,
            source_ref=effective_context.source_ref,
            actor=effective_context.actor,
            agent_key=effective_context.agent_key,
            conversation_id=effective_context.conversation_id,
            turn_id=effective_context.turn_id,
            trust=effective_context.trust,
            existing_candidates=conflict_candidates,
            llm_adjudication=effective_context.llm_adjudication,
        )

        candidate = MemoryWriteCandidate(
            fact=normalized,
            text_hash=text_hash,
            kind=kind,
            scope_type=effective_scope,
            scope_id=effective_scope_id,
            project_id=effective_project_id,
            agent_key=resolved_agent_key,
            confidence=float(confidence),
            tags=tuple(str(t) for t in tags),
            supersedes_id=target_supersede,
        )
        decision = self._write_policy.decide(candidate, policy_context)
        if quarantine_enabled is False and decision.action == "quarantine":
            decision = ConflictResolver().decide(candidate, policy_context)

        scope_predicate = (
            agent_memories.c.project_id == effective_project_id
            if effective_project_id
            else agent_memories.c.project_id.is_(None)
        )
        agent_key_predicate = (
            agent_memories.c.agent_key == resolved_agent_key
            if resolved_agent_key
            else agent_memories.c.agent_key.is_(None)
        )

        with self._engine.begin() as connection:
            # D-RF4: Check tombstone for same scope + text_hash before writing.
            tombstone_row = connection.execute(
                select(agent_memories).where(
                    agent_memories.c.user_id == self._user_id,
                    agent_memories.c.scope_type == effective_scope,
                    agent_memories.c.scope_id == effective_scope_id,
                    scope_predicate,
                    agent_key_predicate,
                    agent_memories.c.text_hash == text_hash,
                    agent_memories.c.status == "deleted",
                )
            ).mappings().first()
            if tombstone_row is not None and effective_context.source_kind != "user":
                return {
                    "memory_id": str(tombstone_row["memory_id"]),
                    "fact": "",
                    "created": False,
                    "superseded": [],
                    "blocked_by_tombstone": True,
                    "status": "deleted",
                }

            # Policy-driven non-default actions (D3 conflict resolution & D4 quarantine).
            if decision.action == "noop" and decision.target_id:
                existing_target = connection.execute(
                    select(agent_memories.c.fact).where(
                        agent_memories.c.memory_id == decision.target_id,
                        agent_memories.c.user_id == self._user_id,
                    )
                ).mappings().first()
                connection.execute(
                    update(agent_memories)
                    .where(
                        agent_memories.c.memory_id == decision.target_id,
                        agent_memories.c.user_id == self._user_id,
                    )
                    .values(last_used_at=now, updated_at=now)
                )
                return {
                    "memory_id": decision.target_id,
                    "fact": str(existing_target["fact"]) if existing_target else normalized,
                    "created": False,
                    "superseded": [],
                    "status": decision.status,
                    "decision": "noop",
                    "reason": decision.reason,
                }

            if decision.action in {"quarantine", "conflict"}:
                memory_id = f"mem_{uuid4().hex}"
                row_status = "quarantined" if decision.action == "quarantine" else "proposed"
                connection.execute(
                    insert(agent_memories).values(
                        memory_id=memory_id,
                        user_id=self._user_id,
                        conversation_id=effective_context.conversation_id,
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
                        status=row_status,
                        agent_key=resolved_agent_key,
                        text_hash=text_hash,
                        valid_from=now,
                        invalid_at=None,
                        source_kind=effective_context.source_kind,
                        source_ref=effective_context.source_ref,
                        trust=effective_context.trust,
                        created_at=now,
                        updated_at=now,
                    )
                )
                event_name = "quarantined" if decision.action == "quarantine" else "conflict"
                self._lifecycle.record_event(
                    connection,
                    memory_id=memory_id,
                    user_id=self._user_id,
                    event=event_name,
                    actor=effective_context.actor,
                    new_text=normalized,
                    related_id=decision.target_id,
                    conversation_id=effective_context.conversation_id,
                    turn_id=effective_context.turn_id,
                    created_at=now,
                )
                if decision.action == "conflict" and decision.target_id:
                    conflicting_row = connection.execute(
                        select(agent_memories.c.fact).where(
                            agent_memories.c.memory_id == decision.target_id,
                            agent_memories.c.user_id == self._user_id,
                        )
                    ).mappings().first()
                    existing_old_fact = str(conflicting_row["fact"]) if conflicting_row else None
                    self._lifecycle.record_event(
                        connection,
                        memory_id=decision.target_id,
                        user_id=self._user_id,
                        event="conflict",
                        actor=effective_context.actor,
                        old_text=existing_old_fact,
                        new_text=normalized,
                        related_id=memory_id,
                        conversation_id=effective_context.conversation_id,
                        turn_id=effective_context.turn_id,
                        created_at=now,
                    )
                if decision.action == "conflict":
                    logger.info(
                        "memory.conflict user_id=%s scope=%s kind=%s reason=%s existing_id=%s proposed_id=%s",
                        self._user_id,
                        effective_scope,
                        kind,
                        decision.reason,
                        decision.target_id,
                        memory_id,
                        extra={
                            "event": "memory.conflict",
                            "user_id": self._user_id,
                            "scope_type": effective_scope,
                            "project_id": effective_project_id,
                            "kind": kind,
                            "reason": decision.reason,
                            "existing_memory_id": decision.target_id,
                            "proposed_memory_id": memory_id,
                            "source_kind": effective_context.source_kind,
                        },
                    )
                receipt: dict[str, object] = {
                    "memory_id": memory_id,
                    "fact": normalized,
                    "created": True,
                    "superseded": [],
                    "status": row_status,
                    "decision": decision.action,
                    "reason": decision.reason,
                    "source_kind": effective_context.source_kind,
                    "source_ref": effective_context.source_ref,
                    "trust": effective_context.trust,
                }
                if decision.action == "conflict" and decision.target_id:
                    receipt["conflict_with"] = decision.target_id
                return receipt

            # D-RF10: Subagent writes in scope_type='agent' enter as 'proposed' waiting for MemoryPromotionGate
            # unless learnFromVerifiedOnly is explicitly disabled (and propose_only is False).
            if effective_scope == "agent":
                require_proposal = (
                    True
                    if propose_only
                    else (
                        bool(learn_from_verified_only)
                        if learn_from_verified_only is not None
                        else is_learn_from_verified_only(self._resolve_settings(), default=True)
                    )
                )
                if require_proposal:
                    memory_id = f"mem_{uuid4().hex}"
                    connection.execute(
                        insert(agent_memories).values(
                            memory_id=memory_id,
                            user_id=self._user_id,
                            conversation_id=effective_context.conversation_id,
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
                            status="proposed",
                            agent_key=resolved_agent_key,
                            text_hash=text_hash,
                            valid_from=now,
                            invalid_at=None,
                            source_kind=effective_context.source_kind,
                            source_ref=effective_context.source_ref,
                            trust=effective_context.trust,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    self._lifecycle.record_event(
                        connection,
                        memory_id=memory_id,
                        user_id=self._user_id,
                        event="proposed",
                        actor=effective_context.actor,
                        new_text=normalized,
                        related_id=target_supersede,
                        conversation_id=effective_context.conversation_id,
                        turn_id=effective_context.turn_id,
                        created_at=now,
                    )
                    return {
                        "memory_id": memory_id,
                        "fact": normalized,
                        "created": True,
                        "superseded": [],
                        "status": "proposed",
                        "scope_type": "agent",
                        "scope_id": effective_scope_id,
                        "agent_key": resolved_agent_key,
                        "source_kind": effective_context.source_kind,
                        "source_ref": effective_context.source_ref,
                        "trust": effective_context.trust,
                    }

            # Exact active match by text_hash in the same scope -> update in-place & audit event
            existing_row = connection.execute(
                select(agent_memories).where(
                    agent_memories.c.user_id == self._user_id,
                    agent_memories.c.scope_type == effective_scope,
                    agent_memories.c.scope_id == effective_scope_id,
                    scope_predicate,
                    agent_key_predicate,
                    agent_memories.c.status == "active",
                    agent_memories.c.text_hash == text_hash,
                )
            ).mappings().first()
            if existing_row is not None:
                existing_id = str(existing_row["memory_id"])
                old_fact = str(existing_row.get("fact") or "")
                merged_tags = _merge_tags(existing_row.get("tags"), tags)
                merged_conf = max(float(existing_row.get("confidence") or 0.0), float(confidence))
                connection.execute(
                    update(agent_memories)
                    .where(agent_memories.c.memory_id == existing_id)
                    .values(
                        fact=normalized,
                        updated_at=now,
                        last_used_at=now,
                        tags=merged_tags,
                        kind=kind,
                        confidence=merged_conf,
                        source=source,
                        emotional_valence=valence,
                        salience=salience_val,
                        superseded_by=None,
                    )
                )
                self._lifecycle.record_event(
                    connection,
                    memory_id=existing_id,
                    user_id=self._user_id,
                    event="updated",
                    actor=effective_context.actor,
                    old_text=old_fact,
                    new_text=normalized,
                    conversation_id=effective_context.conversation_id,
                    turn_id=effective_context.turn_id,
                    created_at=now,
                )
                superseded: list[str] = []
                supersede_targets: list[str] = []
                if decision.action == "supersede" and decision.target_id and decision.target_id != existing_id:
                    supersede_targets.append(decision.target_id)
                if target_supersede and target_supersede != existing_id and target_supersede not in supersede_targets:
                    supersede_targets.append(target_supersede)
                for old_target_id in supersede_targets:
                    superseded.extend(
                        self._supersede_by_id(
                            connection,
                            old_target_id,
                            existing_id,
                            now,
                            actor=effective_context.actor,
                            new_text=normalized,
                            conversation_id=effective_context.conversation_id,
                            turn_id=effective_context.turn_id,
                        )
                    )
                return {
                    "memory_id": existing_id,
                    "fact": normalized,
                    "created": False,
                    "superseded": superseded,
                    "status": "active",
                    "source_kind": effective_context.source_kind,
                    "source_ref": effective_context.source_ref,
                    "trust": effective_context.trust,
                }

            # Policy-driven explicit supersede target
            if decision.action == "supersede" and decision.target_id:
                memory_id = f"mem_{uuid4().hex}"
                old_target_row = connection.execute(
                    select(agent_memories).where(
                        agent_memories.c.memory_id == decision.target_id,
                        agent_memories.c.user_id == self._user_id,
                    )
                ).mappings().first()
                effective_tags = (
                    _merge_tags(old_target_row.get("tags"), tags)
                    if old_target_row is not None and decision.reason == "near_duplicate_or_replacement"
                    else list(tags)
                )
                effective_conf = (
                    max(float(old_target_row.get("confidence") or 0.0), float(confidence))
                    if old_target_row is not None and decision.reason == "near_duplicate_or_replacement"
                    else float(confidence)
                )
                superseded = self._supersede_by_id(
                    connection,
                    decision.target_id,
                    memory_id,
                    now,
                    actor=effective_context.actor,
                    old_text=str(old_target_row.get("fact") or "") if old_target_row is not None else None,
                    new_text=normalized,
                    conversation_id=effective_context.conversation_id,
                    turn_id=effective_context.turn_id,
                )
                if target_supersede and target_supersede != decision.target_id:
                    superseded.extend(
                        self._supersede_by_id(
                            connection,
                            target_supersede,
                            memory_id,
                            now,
                            actor=effective_context.actor,
                            new_text=normalized,
                            conversation_id=effective_context.conversation_id,
                            turn_id=effective_context.turn_id,
                        )
                    )
                connection.execute(
                    insert(agent_memories).values(
                        memory_id=memory_id,
                        user_id=self._user_id,
                        conversation_id=effective_context.conversation_id,
                        scope_type=effective_scope,
                        scope_id=effective_scope_id,
                        project_id=effective_project_id,
                        source_message_id=None,
                        source_execution_id=self._execution_id,
                        fact=normalized,
                        tags=effective_tags,
                        kind=kind,
                        confidence=effective_conf,
                        emotional_valence=valence,
                        salience=salience_val,
                        source=source,
                        hit_count=0,
                        last_used_at=None,
                        superseded_by=None,
                        status=decision.status or "active",
                        agent_key=resolved_agent_key,
                        text_hash=text_hash,
                        valid_from=now,
                        invalid_at=None,
                        source_kind=effective_context.source_kind,
                        source_ref=effective_context.source_ref,
                        trust=effective_context.trust,
                        created_at=now,
                        updated_at=now,
                    )
                )
                self._lifecycle.record_event(
                    connection,
                    memory_id=memory_id,
                    user_id=self._user_id,
                    event="created",
                    actor=effective_context.actor,
                    new_text=normalized,
                    related_id=decision.target_id,
                    conversation_id=effective_context.conversation_id,
                    turn_id=effective_context.turn_id,
                    created_at=now,
                )
                return {
                    "memory_id": memory_id,
                    "fact": normalized,
                    "created": True,
                    "superseded": superseded,
                    "status": decision.status or "active",
                    "decision": "supersede",
                    "reason": decision.reason,
                    "source_kind": effective_context.source_kind,
                    "source_ref": effective_context.source_ref,
                    "trust": effective_context.trust,
                }

            # D-RF3: Near-duplicate creates a new version and supersedes the old one, preserving old_text in memory_events.
            near_dup = self._find_near_duplicate(
                connection,
                normalized,
                kind,
                scope_type=effective_scope,
                project_id=effective_project_id,
                agent_key=resolved_agent_key,
                exclude_id=target_supersede,
            )
            if near_dup is not None:
                dup_id = str(near_dup["memory_id"])
                dup_old_fact = str(near_dup.get("fact") or "")
                merged_tags = _merge_tags(near_dup.get("tags"), tags)
                merged_conf = max(float(near_dup.get("confidence") or 0.0), float(confidence))
                memory_id = f"mem_{uuid4().hex}"
                superseded = self._supersede_by_id(
                    connection,
                    dup_id,
                    memory_id,
                    now,
                    actor=effective_context.actor,
                    old_text=dup_old_fact,
                    new_text=normalized,
                    conversation_id=effective_context.conversation_id,
                    turn_id=effective_context.turn_id,
                )
                if target_supersede and target_supersede != dup_id:
                    superseded.extend(
                        self._supersede_by_id(
                            connection,
                            target_supersede,
                            memory_id,
                            now,
                            actor=effective_context.actor,
                            new_text=normalized,
                            conversation_id=effective_context.conversation_id,
                            turn_id=effective_context.turn_id,
                        )
                    )
                connection.execute(
                    insert(agent_memories).values(
                        memory_id=memory_id,
                        user_id=self._user_id,
                        conversation_id=effective_context.conversation_id,
                        scope_type=effective_scope,
                        scope_id=effective_scope_id,
                        project_id=effective_project_id,
                        source_message_id=None,
                        source_execution_id=self._execution_id,
                        fact=normalized,
                        tags=merged_tags,
                        kind=kind,
                        confidence=merged_conf,
                        emotional_valence=valence,
                        salience=salience_val,
                        source=source,
                        hit_count=0,
                        last_used_at=None,
                        superseded_by=None,
                        status=decision.status or "active",
                        agent_key=resolved_agent_key,
                        text_hash=text_hash,
                        valid_from=now,
                        invalid_at=None,
                        source_kind=effective_context.source_kind,
                        source_ref=effective_context.source_ref,
                        trust=effective_context.trust,
                        created_at=now,
                        updated_at=now,
                    )
                )
                self._lifecycle.record_event(
                    connection,
                    memory_id=memory_id,
                    user_id=self._user_id,
                    event="created",
                    actor=effective_context.actor,
                    new_text=normalized,
                    related_id=dup_id,
                    conversation_id=effective_context.conversation_id,
                    turn_id=effective_context.turn_id,
                    created_at=now,
                )
                return {
                    "memory_id": memory_id,
                    "fact": normalized,
                    "created": True,
                    "superseded": superseded,
                    "status": decision.status or "active",
                    "source_kind": effective_context.source_kind,
                    "source_ref": effective_context.source_ref,
                    "trust": effective_context.trust,
                }

            memory_id = f"mem_{uuid4().hex}"
            superseded = (
                self._supersede_by_id(
                    connection,
                    target_supersede,
                    memory_id,
                    now,
                    actor=effective_context.actor,
                    new_text=normalized,
                    conversation_id=effective_context.conversation_id,
                    turn_id=effective_context.turn_id,
                )
                if target_supersede
                else []
            )
            effective_status = decision.status or "active"
            connection.execute(
                insert(agent_memories).values(
                    memory_id=memory_id,
                    user_id=self._user_id,
                    conversation_id=effective_context.conversation_id,
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
                    status=effective_status,
                    agent_key=resolved_agent_key,
                    text_hash=text_hash,
                    valid_from=now,
                    invalid_at=None,
                    source_kind=effective_context.source_kind,
                    source_ref=effective_context.source_ref,
                    trust=effective_context.trust,
                    created_at=now,
                    updated_at=now,
                )
            )
            initial_event = (
                "proposed"
                if effective_status == "proposed"
                else ("quarantined" if effective_status == "quarantined" else "created")
            )
            self._lifecycle.record_event(
                connection,
                memory_id=memory_id,
                user_id=self._user_id,
                event=initial_event,
                actor=effective_context.actor,
                new_text=normalized,
                related_id=target_supersede,
                conversation_id=effective_context.conversation_id,
                turn_id=effective_context.turn_id,
                created_at=now,
            )
        return {
            "memory_id": memory_id,
            "fact": normalized,
            "created": True,
            "superseded": superseded,
            "status": effective_status,
            "source_kind": effective_context.source_kind,
            "source_ref": effective_context.source_ref,
            "trust": effective_context.trust,
        }

    def _find_conflict_candidates(
        self,
        fact: str,
        kind: str,
        *,
        scope_type: str,
        project_id: str | None,
        agent_key: str | None = None,
        scope_id: str | None = None,
        text_hash: str,
        limit: int = MAX_CONFLICT_CANDIDATES,
    ) -> tuple[ExistingMemoryCandidate, ...]:
        """Return up to ``limit`` (5) active memories in the same scope and kind most similar to ``fact``."""
        scope_predicate = (
            agent_memories.c.project_id == project_id
            if project_id
            else agent_memories.c.project_id.is_(None)
        )
        agent_key_predicate = (
            agent_memories.c.agent_key == agent_key
            if agent_key
            else agent_memories.c.agent_key.is_(None)
        )
        predicates = [
            agent_memories.c.user_id == self._user_id,
            agent_memories.c.scope_type == scope_type,
            agent_memories.c.kind == kind,
            agent_memories.c.status == "active",
            agent_memories.c.superseded_by.is_(None),
            scope_predicate,
            agent_key_predicate,
        ]
        if scope_id is not None:
            predicates.append(agent_memories.c.scope_id == scope_id)
        with self._engine.connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    select(agent_memories).where(*predicates)
                )
                .mappings()
                .all()
            ]
        if not rows:
            return ()

        fts_ranks: dict[str, float] = {}
        sem_scores: dict[str, float] = {}
        try:
            self._hybrid_index.sync(rows, prune_missing=False)
            search_limit = max(25, len(rows))
            fts_ranks = dict(self._hybrid_index.search_lexical(fact, limit=search_limit))
            embedder = getattr(self._hybrid_index, "_embedder", None)
            if embedder is not None and getattr(embedder.identity, "embedder_id", "lexical") != "lexical":
                q_vecs = embedder.embed([fact])
                if q_vecs and q_vecs[0]:
                    sem_hits = self._hybrid_index.search_vector(
                        q_vecs[0],
                        limit=search_limit,
                        min_similarity=0.0,
                    )
                    sem_scores = dict(sem_hits)
        except Exception:  # noqa: BLE001 - hybrid index issues must not block memory writes
            pass

        scored: list[tuple[tuple[int, int, float, float, int, float], ExistingMemoryCandidate]] = []
        for row in rows:
            mid = str(row.get("memory_id") or "")
            if not mid:
                continue
            row_fact = str(row.get("fact") or "")
            row_hash = str(row.get("text_hash") or "").strip() or compute_text_hash(row_fact)
            exact = 1 if row_hash == text_hash else 0
            sem = sem_scores.get(mid)
            jac = compute_jaccard(fact, row_fact)
            short_rep = 1 if _is_short_slot_replacement(fact, row_fact) else 0
            fts_hit = 1 if mid in fts_ranks else 0
            fts_score = -fts_ranks[mid] if mid in fts_ranks else 0.0
            if not (exact or short_rep or jac > 0.0 or fts_hit or (sem is not None and sem >= 0.50)):
                continue
            cand = ExistingMemoryCandidate.from_any(row, semantic_similarity=sem)
            key = (exact, short_rep, sem if sem is not None else 0.0, jac, fts_hit, fts_score)
            scored.append((key, cand))

        scored.sort(key=lambda item: item[0], reverse=True)
        return tuple(cand for _, cand in scored[: max(1, int(limit))])

    def _find_near_duplicate(
        self,
        connection,
        fact: str,
        kind: str,
        *,
        scope_type: str,
        project_id: str | None,
        agent_key: str | None = None,
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
        agent_key_predicate = (
            agent_memories.c.agent_key == agent_key
            if agent_key
            else agent_memories.c.agent_key.is_(None)
        )
        rows = connection.execute(
            select(agent_memories).where(
                agent_memories.c.user_id == self._user_id,
                agent_memories.c.scope_type == scope_type,
                agent_memories.c.kind == kind,
                agent_memories.c.status == "active",
                agent_memories.c.superseded_by.is_(None),
                scope_predicate,
                agent_key_predicate,
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
        *,
        actor: str = "system",
        old_text: str | None = None,
        new_text: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
    ) -> list[str]:
        if not old_memory_id or old_memory_id == replacement_id:
            return []
        ok = self._lifecycle.supersede(
            connection,
            old_memory_id,
            replacement_id,
            actor=actor,
            old_text=old_text,
            new_text=new_text,
            user_id=self._user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            now=now,
        )
        return [old_memory_id] if ok else []

    def supersede_memory(self, old_memory_id: str, new_memory_id: str, *, actor: str = "system") -> bool:
        """Explicitly chain ``old_memory_id`` to ``new_memory_id`` if both belong to this user.

        Requires ``new_memory_id`` to be active and refuses non-user actors from superseding
        protected ``source_kind='user'`` memories.
        """
        old_id = str(old_memory_id or "").strip()
        new_id = str(new_memory_id or "").strip()
        if not old_id or not new_id or old_id == new_id:
            return False
        now = datetime.now(UTC)
        clean_actor = str(actor or "system").strip()
        is_user_actor = clean_actor == "user" or clean_actor.startswith("user:")
        with self._engine.begin() as connection:
            new_row = connection.execute(
                select(agent_memories.c.memory_id, agent_memories.c.fact, agent_memories.c.status).where(
                    agent_memories.c.memory_id == new_id,
                    agent_memories.c.user_id == self._user_id,
                    agent_memories.c.status == "active",
                )
            ).mappings().first()
            if new_row is None:
                return False
            old_row = connection.execute(
                select(
                    agent_memories.c.memory_id,
                    agent_memories.c.status,
                    agent_memories.c.source_kind,
                ).where(
                    agent_memories.c.memory_id == old_id,
                    agent_memories.c.user_id == self._user_id,
                )
            ).mappings().first()
            if old_row is None or str(old_row.get("status") or "") != "active":
                return False
            if str(old_row.get("source_kind") or "") == "user" and not is_user_actor:
                return False
            replaced = self._supersede_by_id(
                connection,
                old_id,
                new_id,
                now,
                actor=clean_actor,
                new_text=str(new_row.get("fact") or "") or None,
                conversation_id=self._conversation_id,
            )
        return bool(replaced)

    def get_memory(
        self,
        memory_id: str,
        *,
        conversation_id: str | None = None,
        record_usage: bool = True,
        include_non_active: bool = False,
    ) -> dict[str, object] | None:
        """Fetch one memory by id strictly isolated to ``self._user_id``.

        Follows supersession chains to return the latest active successor with
        ``superseded_from: <memory_id>``; returns a clear ``memória esquecida``
        marker when the memory (or its terminal successor) was tombstoned.
        Quarantined, proposed, and invalid entries are hidden unless
        ``include_non_active=True`` (used by the review API).
        """
        target_id = str(memory_id or "").strip()
        if not target_id:
            return None
        terminal, followed = self._lifecycle.resolve_chain(target_id, self._user_id)
        if terminal is None:
            return None
        status = str(terminal.get("status") or "active")
        if status == "deleted":
            marker: dict[str, object] = {
                "memory_id": str(terminal["memory_id"]),
                "status": "deleted",
                "deleted": True,
                "error": "memória esquecida",
                "message": "memória esquecida",
                "fact": "",
                "scope_type": terminal.get("scope_type", "user"),
                "project_id": terminal.get("project_id"),
            }
            if followed:
                marker["superseded_from"] = target_id
            return marker
        if not include_non_active and status != "active":
            return None
        if status == "active" and record_usage:
            self._record_usage([terminal], conversation_id=conversation_id)
        public = self._public(terminal)
        if followed:
            public["superseded_from"] = target_id
        return public

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

    def active_for_agent_scope(self, scope: AgentMemoryScope) -> list[dict[str, object]]:
        """Return all active, non-superseded memories strictly matching ``scope``."""
        proj_pred = (
            agent_memories.c.project_id == scope.effective_project_id
            if scope.effective_project_id is not None
            else agent_memories.c.project_id.is_(None)
        )
        stmt = (
            select(agent_memories)
            .where(
                agent_memories.c.user_id == scope.user_id,
                agent_memories.c.scope_type == "agent",
                agent_memories.c.scope_id == scope.scope_id,
                agent_memories.c.agent_key == scope.agent_key,
                proj_pred,
                agent_memories.c.status == "active",
                agent_memories.c.superseded_by.is_(None),
            )
            .order_by(agent_memories.c.updated_at.desc())
        )
        with self._engine.connect() as connection:
            return [self._public(dict(row)) for row in connection.execute(stmt).mappings().all()]

    def relevant(
        self,
        task: str,
        *,
        limit: int = 12,
        conversation_id: str | None = None,
        agent_key: str | None = None,
        agent_scope: AgentMemoryScope | None = None,
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
            for row in self._all(agent_key=agent_key, agent_scope=agent_scope)
            if str(row.get("status") or "active") == "active"
            and row["superseded_by"] is None
            and not (row.get("source") == "mechanical" and is_ephemeral_mechanical_fact(str(row.get("fact") or "")))
        ]
        if not rows:
            return []

        final_scores, rrf_scores = self._hybrid_index.rank(
            task,
            rows,
            all_user_rows=self._iter_active_user_rows_flat(),
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

        layer1_rows = [
            row for row in self._all()
            if str(row.get("status") or "active") == "active" and row["superseded_by"] is None
        ]
        layer2_rows = [
            row for row in self._all_friend()
            if str(row.get("status") or "active") == "active" and row["superseded_by"] is None
        ]

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

    @staticmethod
    def _recall_scope_bonus(row: dict[str, object]) -> float:
        st = str(row.get("scope_type") or "user")
        if st == "agent":
            return 0.35
        if st == "project":
            return 0.15
        return 0.0

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        scope: str | None = None,
        conversation_id: str | None = None,
        agent_key: str | None = None,
        agent_scope: AgentMemoryScope | None = None,
    ) -> list[dict[str, object]]:
        if limit <= 0:
            return []
        norm_scope = (
            str(scope).strip().lower()
            if scope is not None and str(scope).strip().lower() in {"user", "project", "agent"}
            else None
        )
        rows = self._all(scope=norm_scope, agent_key=agent_key, agent_scope=agent_scope)
        if not rows:
            return []

        has_agent_ctx = agent_scope is not None or bool(agent_key)
        now = datetime.now(UTC)
        if not str(query or "").strip():
            if has_agent_ctx and norm_scope is None:
                rows.sort(key=lambda r: (self._recall_scope_bonus(r), r["updated_at"]), reverse=True)
            selected = rows[:limit]
            self._record_usage(selected, conversation_id=conversation_id)
            return [self._public(row) for row in selected]

        final_scores, rrf_scores = self._hybrid_index.rank(
            query,
            rows,
            all_user_rows=self._iter_active_user_rows_flat(),
            active_project_id=self._project_id,
            now=now,
            min_vector_similarity=0.25,
        )
        matched_rows = [
            row for row in rows if rrf_scores.get(str(row["memory_id"]), 0.0) > 0.0
        ]
        if not matched_rows:
            return []
        if has_agent_ctx and norm_scope is None:
            matched_rows.sort(
                key=lambda row: (
                    final_scores.get(str(row["memory_id"]), 0.0) + self._recall_scope_bonus(row),
                    self._recall_scope_bonus(row),
                    row["updated_at"],
                ),
                reverse=True,
            )
        else:
            matched_rows.sort(
                key=lambda row: (final_scores.get(str(row["memory_id"]), 0.0), row["updated_at"]),
                reverse=True,
            )
        selected = matched_rows[:limit]
        self._record_usage(selected, conversation_id=conversation_id)
        return [self._public(row) for row in selected]

    def recent(self, *, limit: int = 12) -> list[dict[str, object]]:
        return [self._public(row) for row in self._all(limit=limit)]

    def list_friend_memories(self, *, limit: int = 50) -> list[dict[str, object]]:
        return [self._public(row) for row in self._all_friend(limit=limit)]

    def forget(self, memory_id: str, *, actor: str = "user") -> bool:
        if self._project_id:
            return self._lifecycle.tombstone(
                memory_id,
                self._user_id,
                actor=actor,
                scope_type="project",
                project_id=self._project_id,
                check_scope=True,
                conversation_id=self._conversation_id,
            )
        return self._lifecycle.tombstone(
            memory_id,
            self._user_id,
            actor=actor,
            scope_types=("user", "friend"),
            project_id=None,
            check_scope=True,
            conversation_id=self._conversation_id,
        )

    def close(self) -> None:
        self._hybrid_index.close()

    def _iter_active_user_rows(self, *, page_size: int = 500) -> Iterator[list[dict[str, object]]]:
        """Yield pages of active user rows (default 500 per page) without any global cap."""
        step = max(1, int(page_size))
        last_id: str | None = None
        while True:
            predicate = [
                agent_memories.c.user_id == self._user_id,
                agent_memories.c.status == "active",
                agent_memories.c.superseded_by.is_(None),
            ]
            if last_id is not None:
                predicate.append(agent_memories.c.memory_id > last_id)
            with self._engine.connect() as connection:
                page = [
                    dict(row)
                    for row in connection.execute(
                        select(agent_memories)
                        .where(*predicate)
                        .order_by(agent_memories.c.memory_id.asc())
                        .limit(step)
                    )
                    .mappings()
                    .all()
                ]
            if not page:
                break
            yield page
            last_id = str(page[-1]["memory_id"])
            if len(page) < step:
                break

    def _iter_active_user_rows_flat(self, *, page_size: int = 500) -> Iterator[dict[str, object]]:
        for page in self._iter_active_user_rows(page_size=page_size):
            yield from page

    def _all_active_user_rows(self) -> list[dict[str, object]]:
        predicate = [
            agent_memories.c.user_id == self._user_id,
            agent_memories.c.status == "active",
            agent_memories.c.superseded_by.is_(None),
        ]
        with self._engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(agent_memories)
                    .where(*predicate)
                    .order_by(agent_memories.c.updated_at.desc())
                )
                .mappings()
                .all()
            ]

    def _agent_scope_clause(
        self,
        *,
        agent_key: str | None = None,
        agent_scope: AgentMemoryScope | None = None,
    ):
        if agent_scope is not None:
            proj_pred = (
                agent_memories.c.project_id == agent_scope.effective_project_id
                if agent_scope.effective_project_id is not None
                else agent_memories.c.project_id.is_(None)
            )
            return (
                (agent_memories.c.scope_type == "agent")
                & (agent_memories.c.scope_id == agent_scope.scope_id)
                & (agent_memories.c.agent_key == agent_scope.agent_key)
                & proj_pred
            )
        clean_key = str(agent_key or "").strip().lower()
        if not clean_key:
            return None
        proj_pred = (
            agent_memories.c.project_id == self._project_id
            if self._project_id
            else agent_memories.c.project_id.is_(None)
        )
        return (
            (agent_memories.c.scope_type == "agent")
            & (agent_memories.c.agent_key == clean_key)
            & proj_pred
        )

    def _all(
        self,
        *,
        scope: str | None = None,
        limit: int | None = None,
        agent_key: str | None = None,
        agent_scope: AgentMemoryScope | None = None,
    ) -> list[dict[str, object]]:
        predicate = [
            agent_memories.c.user_id == self._user_id,
            agent_memories.c.status == "active",
            agent_memories.c.superseded_by.is_(None),
        ]
        agent_clause = self._agent_scope_clause(agent_key=agent_key, agent_scope=agent_scope)
        if scope == "agent":
            if agent_clause is None:
                return []
            predicate.append(agent_clause)
        elif scope == "user":
            predicate.extend((agent_memories.c.scope_type == "user", agent_memories.c.project_id.is_(None)))
        elif scope == "project":
            if self._project_id:
                predicate.extend(
                    (agent_memories.c.scope_type == "project", agent_memories.c.project_id == self._project_id)
                )
            else:
                return []
        else:
            base_clause = (
                (agent_memories.c.scope_type == "user")
                | ((agent_memories.c.scope_type == "project") & (agent_memories.c.project_id == self._project_id))
                if self._project_id
                else ((agent_memories.c.scope_type == "user") & agent_memories.c.project_id.is_(None))
            )
            predicate.append((agent_clause | base_clause) if agent_clause is not None else base_clause)
        stmt = select(agent_memories).where(*predicate).order_by(agent_memories.c.updated_at.desc())
        if limit is not None:
            stmt = stmt.limit(max(0, int(limit)))
        with self._engine.connect() as connection:
            return [dict(row) for row in connection.execute(stmt).mappings().all()]

    def _all_friend(self, *, limit: int | None = None) -> list[dict[str, object]]:
        predicate = [
            agent_memories.c.user_id == self._user_id,
            agent_memories.c.scope_type == "friend",
            agent_memories.c.status == "active",
            agent_memories.c.superseded_by.is_(None),
        ]
        stmt = select(agent_memories).where(*predicate).order_by(agent_memories.c.updated_at.desc())
        if limit is not None:
            stmt = stmt.limit(max(0, int(limit)))
        with self._engine.connect() as connection:
            return [dict(row) for row in connection.execute(stmt).mappings().all()]

    @staticmethod
    def _public(row: dict[str, object]) -> dict[str, object]:
        updated = row.get("updated_at")
        last_used = row.get("last_used_at")
        valid_from = row.get("valid_from")
        return {
            "memory_id": row["memory_id"],
            "fact": row["fact"],
            "tags": list(row["tags"] or []),
            "kind": row["kind"],
            "scope_type": row.get("scope_type", "user"),
            "scope_id": row.get("scope_id"),
            "project_id": row.get("project_id"),
            "agent_key": row.get("agent_key"),
            "status": row.get("status", "active"),
            "source_kind": row.get("source_kind", "agent"),
            "valid_from": valid_from.isoformat() if isinstance(valid_from, datetime) else valid_from,
            "trust": int(row["trust"]) if row.get("trust") is not None else 1,
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
