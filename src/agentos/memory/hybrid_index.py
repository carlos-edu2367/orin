"""Derived hybrid index (FTS5 BM25 + dense vectors + RRF) for user and project memories.

The primary table ``agent_memories`` remains the single source of truth. This
SQLite WAL index lives alongside the code retrieval index and can be rebuilt at
any time from active memory rows.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock, RLock

import numpy as np

from agentos.installation.paths import orin_paths
from agentos.retrieval.embeddings.lexical import LexicalOnlyEmbedder
from agentos.retrieval.factory import EMBEDDER_VARIABLE, build_embedder
from agentos.retrieval.models import EmbedderIdentity
from agentos.retrieval.ports import EmbeddingPort, EmbeddingUnavailable
from agentos.retrieval.service import RRF_K, reciprocal_rank_fusion


SCHEMA_VERSION = "1"
MAX_QUERY_TOKENS = 32

_WORD = re.compile(r"[\wÀ-ÿ]{3,}", re.UNICODE)
_FTS_TOKEN = re.compile(r"[\wÀ-ÿ]{2,}", re.UNICODE)
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "was", "were", "are", "you", "your",
    "que", "com", "para", "uma", "dos", "das", "por", "sobre", "como", "meu", "minha", "sua", "seu",
})

_PATH_LOCKS_GUARD = Lock()
_PATH_LOCKS: dict[str, RLock] = {}


def _lock_for_path(path: Path) -> RLock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _PATH_LOCKS[key] = lock
        return lock

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories_meta (
    memory_id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    updated_at_iso TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    memory_id UNINDEXED, fact, tags, tokenize = 'unicode61'
);
CREATE TABLE IF NOT EXISTS memory_vectors (
    memory_id TEXT PRIMARY KEY,
    embedding BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _terms(text_val: str) -> set[str]:
    return {word.lower() for word in _WORD.findall(text_val or "")} - _STOPWORDS


def fts_memory_query(text_val: str) -> str:
    raw_tokens = [tok.lower() for tok in _FTS_TOKEN.findall(text_val or "")]
    filtered = [tok for tok in raw_tokens if tok not in _STOPWORDS]
    tokens = (filtered or raw_tokens)[:MAX_QUERY_TOKENS]
    seen: list[str] = []
    for tok in tokens:
        if tok not in seen:
            seen.append(tok)
    return " OR ".join(f'"{tok}"' for tok in seen)


def memory_index_path_for(user_id: str, *, data_dir: Path | str | None = None) -> Path:
    digest = hashlib.sha256(str(user_id or "default").encode("utf-8")).hexdigest()[:16]
    root = Path(data_dir) if data_dir is not None else orin_paths().data
    return root / "retrieval" / f"memories_{digest}.db"


def _default_memory_embedder() -> EmbeddingPort:
    configured = os.getenv(EMBEDDER_VARIABLE, "").strip().lower()
    if configured in {"ollama", "remote"}:
        return build_embedder()
    return LexicalOnlyEmbedder()


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def _row_signature(row: Mapping[str, object]) -> tuple[str, str, str, str, str, str, str]:
    fact = str(row.get("fact") or "")
    tags_raw = row.get("tags") or ()
    tags_text = " ".join(str(tag) for tag in tags_raw)
    scope_type = str(row.get("scope_type") or "user")
    project_id = str(row["project_id"]) if row.get("project_id") is not None else ""
    kind = str(row.get("kind") or "fact")
    updated_raw = row.get("updated_at")
    updated_at_iso = (
        updated_raw.isoformat()
        if isinstance(updated_raw, datetime)
        else str(updated_raw or "")
    )
    digest = hashlib.sha256(
        f"{fact}\x00{tags_text}\x00{scope_type}\x00{project_id}\x00{kind}".encode("utf-8")
    ).hexdigest()[:32]
    return fact, tags_text, scope_type, project_id, kind, updated_at_iso, digest


class MemoryHybridIndex:
    """SQLite FTS5 + dense vector index for one user's memories."""

    def __init__(
        self,
        user_id: str,
        *,
        index_path: Path | str | None = None,
        data_dir: Path | str | None = None,
        embedder: EmbeddingPort | None = None,
    ) -> None:
        self._user_id = str(user_id or "default")
        self._in_memory = str(index_path) == ":memory:"
        if self._in_memory:
            self._path = Path(":memory:")
        elif index_path is not None:
            self._path = Path(index_path)
            self._path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self._path = memory_index_path_for(self._user_id, data_dir=data_dir)
            self._path.parent.mkdir(parents=True, exist_ok=True)

        self._embedder: EmbeddingPort = embedder if embedder is not None else _default_memory_embedder()
        self._lock = RLock() if self._in_memory else _lock_for_path(self._path)
        self._mem_conn: sqlite3.Connection | None = None
        if self._in_memory:
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        self._matrix_cache: tuple[list[str], np.ndarray] | None = None
        self._synced_signatures: dict[str, tuple[str, str]] = {}
        self._missing_vectors: set[str] = set()

        with self._lock, self._connect() as conn:
            if not self._in_memory:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(_SCHEMA)
            self._reconcile_identity_conn(conn)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def embedder(self) -> EmbeddingPort:
        return self._embedder

    @embedder.setter
    def embedder(self, value: EmbeddingPort | None) -> None:
        with self._lock:
            self._embedder = value if value is not None else _default_memory_embedder()
            with self._connect() as conn:
                self._reconcile_identity_conn(conn)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._in_memory:
            assert self._mem_conn is not None
            yield self._mem_conn
            return
        conn = sqlite3.connect(str(self._path), timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            conn.close()

    def _reconcile_identity_conn(self, conn: sqlite3.Connection) -> None:
        identity: EmbedderIdentity = self._embedder.identity
        stored = {
            str(row["key"]): str(row["value"])
            for row in conn.execute("SELECT key, value FROM index_meta")
        }
        current = {
            "schema_version": SCHEMA_VERSION,
            "embedder_id": identity.embedder_id,
            "model": identity.model,
            "dim": str(identity.dim),
        }
        if stored and stored.get("schema_version") != SCHEMA_VERSION:
            with conn:
                conn.execute("DELETE FROM memory_vectors")
                conn.execute("DELETE FROM memories_fts")
                conn.execute("DELETE FROM memories_meta")
            self._synced_signatures.clear()
            self._missing_vectors.clear()
        elif stored and (
            stored.get("embedder_id") != current["embedder_id"]
            or stored.get("model") != current["model"]
            or (identity.dim > 0 and stored.get("dim") not in {"0", current["dim"]})
        ):
            with conn:
                conn.execute("DELETE FROM memory_vectors")
            self._missing_vectors = set(self._synced_signatures.keys())
        with conn:
            for key, value in current.items():
                conn.execute(
                    "INSERT INTO index_meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
        self._matrix_cache = None

    def sync(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        prune_missing: bool = True,
    ) -> None:
        """Incrementally synchronize the index with active rows from agent_memories."""
        with self._lock:
            incoming_sigs: dict[str, tuple[str, str]] = {}
            parsed_rows: dict[str, tuple[str, str, str, str, str, str, str]] = {}
            for row in rows:
                mid = str(row.get("memory_id") or "")
                if not mid:
                    continue
                sig = _row_signature(row)
                incoming_sigs[mid] = (sig[5], sig[6])
                parsed_rows[mid] = sig

            if incoming_sigs != self._synced_signatures:
                self._matrix_cache = None

            with self._connect() as conn:
                existing_meta = {
                    str(r["memory_id"]): (str(r["updated_at_iso"]), str(r["content_hash"]))
                    for r in conn.execute(
                        "SELECT memory_id, updated_at_iso, content_hash FROM memories_meta"
                    )
                }
                existing_vectors = {
                    str(r["memory_id"])
                    for r in conn.execute("SELECT memory_id FROM memory_vectors")
                }

                removed_ids = (
                    set(existing_meta.keys()) - set(incoming_sigs.keys())
                    if prune_missing
                    else set()
                )
                changed_or_new_ids = {
                    mid
                    for mid, sig_pair in incoming_sigs.items()
                    if existing_meta.get(mid) != sig_pair
                }

                if not removed_ids and not changed_or_new_ids and not self._missing_vectors:
                    need_check = [mid for mid in incoming_sigs if mid not in existing_vectors]
                    if not need_check or self._embedder.identity.embedder_id == "lexical":
                        self._synced_signatures = dict(existing_meta) if not prune_missing else incoming_sigs
                        return

                if removed_ids or changed_or_new_ids:
                    with conn:
                        for mid in removed_ids:
                            conn.execute("DELETE FROM memories_meta WHERE memory_id = ?", (mid,))
                            conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (mid,))
                            conn.execute("DELETE FROM memory_vectors WHERE memory_id = ?", (mid,))
                            existing_vectors.discard(mid)
                        for mid in changed_or_new_ids:
                            fact, tags_text, scope_type, project_id, kind, updated_at_iso, content_hash = parsed_rows[mid]
                            conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (mid,))
                            conn.execute("DELETE FROM memory_vectors WHERE memory_id = ?", (mid,))
                            existing_vectors.discard(mid)
                            conn.execute(
                                "INSERT INTO memories_meta(memory_id, scope_type, project_id, kind, updated_at_iso, content_hash) "
                                "VALUES(?, ?, ?, ?, ?, ?) "
                                "ON CONFLICT(memory_id) DO UPDATE SET "
                                "scope_type=excluded.scope_type, project_id=excluded.project_id, "
                                "kind=excluded.kind, updated_at_iso=excluded.updated_at_iso, "
                                "content_hash=excluded.content_hash",
                                (mid, scope_type, project_id, kind, updated_at_iso, content_hash),
                            )
                            conn.execute(
                                "INSERT INTO memories_fts(memory_id, fact, tags) VALUES(?, ?, ?)",
                                (mid, fact, tags_text),
                            )
                    self._matrix_cache = None

                need_vectors = [
                    mid for mid in incoming_sigs if mid not in existing_vectors
                ]
                if need_vectors and self._embedder.identity.embedder_id != "lexical":
                    texts = [
                        f"{parsed_rows[mid][0]} {parsed_rows[mid][1]}".strip()
                        for mid in need_vectors
                    ]
                    try:
                        vectors = self._embedder.embed(texts)
                        if vectors and len(vectors) == len(need_vectors):
                            with conn:
                                for mid, values in zip(need_vectors, vectors):
                                    vec = np.asarray(values, dtype=np.float32)
                                    norm = float(np.linalg.norm(vec))
                                    normalized = (vec / norm).astype(np.float32) if norm > 0.0 else vec.astype(np.float32)
                                    conn.execute(
                                        "INSERT INTO memory_vectors(memory_id, embedding) VALUES(?, ?) "
                                        "ON CONFLICT(memory_id) DO UPDATE SET embedding=excluded.embedding",
                                        (mid, normalized.tobytes()),
                                    )
                                if vectors[0]:
                                    conn.execute(
                                        "INSERT INTO index_meta(key, value) VALUES('dim', ?) "
                                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                        (str(len(vectors[0])),),
                                    )
                            self._matrix_cache = None
                            self._missing_vectors.clear()
                        else:
                            self._missing_vectors = set(need_vectors)
                    except (EmbeddingUnavailable, Exception):
                        self._missing_vectors = set(need_vectors)
                else:
                    self._missing_vectors = set(need_vectors) if self._embedder.identity.embedder_id != "lexical" else set()

                self._synced_signatures = (
                    incoming_sigs if prune_missing else {**existing_meta, **incoming_sigs}
                )

    def search_lexical(self, query: str, *, limit: int = 50) -> list[tuple[str, float]]:
        expression = fts_memory_query(query)
        if not expression:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT memory_id, bm25(memories_fts) AS rank "
                "FROM memories_fts WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
                (expression, max(1, int(limit))),
            ).fetchall()
            return [(str(row["memory_id"]), float(row["rank"])) for row in rows]

    def search_vector(
        self,
        query_vector: Sequence[float],
        *,
        limit: int = 50,
        min_similarity: float = 0.0,
    ) -> list[tuple[str, float]]:
        with self._lock:
            memory_ids, matrix = self._vector_matrix()
            if not memory_ids:
                return []
            query = np.asarray(query_vector, dtype=np.float32)
            magnitude = float(np.linalg.norm(query))
            if magnitude == 0.0 or query.shape[0] != matrix.shape[1]:
                return []
            scores = matrix @ (query / magnitude)
            count = min(max(1, int(limit)), scores.shape[0])
            best = (
                np.argpartition(-scores, count - 1)[:count]
                if count < scores.shape[0]
                else np.arange(scores.shape[0])
            )
            ordered = best[np.argsort(-scores[best])]
            return [
                (memory_ids[idx], float(scores[idx]))
                for idx in ordered
                if float(scores[idx]) >= min_similarity
            ]

    def _vector_matrix(self) -> tuple[list[str], np.ndarray]:
        cached = self._matrix_cache
        if cached is not None:
            return cached
        memory_ids: list[str] = []
        rows: list[np.ndarray] = []
        with self._connect() as conn:
            for row in conn.execute(
                "SELECT memory_id, embedding FROM memory_vectors ORDER BY memory_id"
            ):
                values = np.frombuffer(row["embedding"], dtype=np.float32)
                if rows and values.shape[0] != rows[0].shape[0]:
                    continue
                memory_ids.append(str(row["memory_id"]))
                rows.append(values)
        matrix = np.vstack(rows) if rows else np.zeros((0, 0), dtype=np.float32)
        self._matrix_cache = (memory_ids, matrix)
        return self._matrix_cache

    def rank(
        self,
        query: str,
        rows: Sequence[Mapping[str, object]],
        *,
        all_user_rows: Sequence[Mapping[str, object]] | None = None,
        prune_missing: bool | None = None,
        active_project_id: str | None = None,
        now: datetime | None = None,
        min_vector_similarity: float = 0.25,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Return ``(final_scores, rrf_scores)`` keyed by ``memory_id`` for ``rows``."""
        if not rows:
            return {}, {}
        moment = now or datetime.now(UTC)
        sync_rows = all_user_rows if all_user_rows is not None else rows
        effective_prune = True if prune_missing is None else bool(prune_missing)
        self.sync(sync_rows, prune_missing=effective_prune)

        candidate_ids = {str(r["memory_id"]) for r in rows if r.get("memory_id")}
        search_limit = max(50, len(sync_rows), len(rows))
        wanted = _terms(query)
        fts_hits = dict(self.search_lexical(query, limit=search_limit))

        lexical_scored: list[tuple[int, float, str]] = []
        for row in rows:
            mid = str(row.get("memory_id") or "")
            if not mid:
                continue
            haystack = _terms(str(row.get("fact") or "")) | {
                str(tag).lower() for tag in (row.get("tags") or ())
            }
            overlap = len(wanted & haystack) if wanted else 0
            bm25_val = fts_hits.get(mid)
            if overlap > 0 or bm25_val is not None:
                # More overlap first; more negative BM25 rank breaks ties by IDF
                lexical_scored.append((-overlap, bm25_val if bm25_val is not None else 0.0, mid))
        lexical_scored.sort()
        bm25_ranking = [mid for _, _, mid in lexical_scored]

        vector_ranking: list[str] = []
        if query and query.strip() and self._embedder.identity.embedder_id != "lexical":
            try:
                q_vecs = self._embedder.embed([query])
                if q_vecs and q_vecs[0]:
                    sem_hits = self.search_vector(
                        q_vecs[0],
                        limit=search_limit,
                        min_similarity=min_vector_similarity,
                    )
                    vector_ranking = [mid for mid, _ in sem_hits if mid in candidate_ids]
            except (EmbeddingUnavailable, Exception):
                vector_ranking = []

        rankings = [r for r in (bm25_ranking, vector_ranking) if r]
        rrf_scores = reciprocal_rank_fusion(rankings, k=RRF_K) if rankings else {}

        final_scores: dict[str, float] = {}
        for row in rows:
            mid = str(row.get("memory_id") or "")
            if not mid:
                continue
            base_rrf = rrf_scores.get(mid, 0.0)
            is_active_proj = bool(
                active_project_id
                and row.get("scope_type") == "project"
                and row.get("project_id") == active_project_id
            )
            project_bonus = 0.008 if is_active_proj else 0.0
            updated_dt = _parse_datetime(row.get("updated_at"))
            age_days = max(0.0, (moment - updated_dt).total_seconds() / 86400.0)
            recency_bonus = 0.004 * math.exp(-age_days / 90.0)
            hit_count = max(0, int(row.get("hit_count") or 0))
            usage_bonus = 0.002 * min(3.0, math.log1p(hit_count))
            conf = max(
                0.0,
                min(1.0, float(row.get("confidence") if row.get("confidence") is not None else 1.0)),
            )
            final_scores[mid] = (base_rrf + project_bonus + recency_bonus + usage_bonus) * (0.7 + 0.3 * conf)

        return final_scores, rrf_scores

    def close(self) -> None:
        with self._lock:
            if self._mem_conn is not None:
                self._mem_conn.close()
                self._mem_conn = None
            self._matrix_cache = None


__all__ = ["MemoryHybridIndex", "SCHEMA_VERSION", "fts_memory_query", "memory_index_path_for"]
