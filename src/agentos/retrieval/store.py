"""The per-project index, in one SQLite file.

Separate from ``orin.db`` on purpose: this is derived, reconstructible data. It
grows fast, it must not enter the Alembic migrations of the domain database, and
deleting the file has to be a safe and complete operation.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
import sqlite3

import numpy as np

from .models import Chunk, EmbedderIdentity, IndexStatus


SCHEMA_VERSION = "1"
MAX_QUERY_TOKENS = 32

_TOKEN = re.compile(r"[A-Za-z0-9_]+")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    language TEXT,
    indexed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    symbol TEXT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_by_path ON chunks(path);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED, symbol, text, tokenize = 'unicode61'
);
CREATE TABLE IF NOT EXISTS vectors (
    chunk_id TEXT PRIMARY KEY,
    embedding BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS imports (
    path TEXT NOT NULL,
    target TEXT NOT NULL,
    PRIMARY KEY (path, target)
);
CREATE INDEX IF NOT EXISTS imports_by_target ON imports(target);
CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def fts_query(text: str) -> str:
    """A safe FTS5 MATCH expression built from the alphanumeric tokens of a query.

    User text reaches FTS5 as syntax, so quotes, hyphens and parentheses in a
    natural-language question would be parsed as operators and raise. Only
    tokens survive, each quoted, joined with OR.
    """
    tokens = _TOKEN.findall(text)[:MAX_QUERY_TOKENS]
    return " OR ".join(f'"{token}"' for token in tokens)


class SqliteChunkStore:
    """Owns the index file. No ranking logic lives here."""

    def __init__(self, database_path: Path | str, identity: EmbedderIdentity) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._identity = identity
        self._connection = sqlite3.connect(str(self._path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(_SCHEMA)
        self._matrix_cache: tuple[list[str], object] | None = None
        self._reconcile_identity()

    # -- identity -------------------------------------------------------

    def _reconcile_identity(self) -> None:
        """Discard vectors produced by a different embedder.

        Stored vectors from another model still return results; they are simply
        wrong. Chunks and the FTS index survive, so lexical search keeps working
        while the vectors are rebuilt.
        """
        stored = {row["key"]: row["value"] for row in self._connection.execute("SELECT key, value FROM index_meta")}
        current = {
            "schema_version": SCHEMA_VERSION,
            "embedder_id": self._identity.embedder_id,
            "model": self._identity.model,
            "dim": str(self._identity.dim),
        }
        if stored and any(stored.get(key) != value for key, value in current.items()):
            self._connection.execute("DELETE FROM vectors")
        with self._connection:
            for key, value in current.items():
                self._connection.execute("INSERT INTO index_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self._matrix_cache = None

    # -- writes ---------------------------------------------------------

    def replace_file(self, path: str, *, content_hash: str, size_bytes: int, mtime_ns: int, language: str | None, chunks: list[Chunk]) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connection:
            self._delete_chunks_of(path)
            self._connection.execute(
                "INSERT INTO files(path, content_hash, size_bytes, mtime_ns, language, indexed_at) VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET content_hash=excluded.content_hash, size_bytes=excluded.size_bytes, "
                "mtime_ns=excluded.mtime_ns, language=excluded.language, indexed_at=excluded.indexed_at",
                (path, content_hash, size_bytes, mtime_ns, language, now),
            )
            for chunk in chunks:
                self._connection.execute(
                    "INSERT INTO chunks(chunk_id, path, start_line, end_line, symbol, kind, text) VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (chunk.chunk_id, chunk.path, chunk.start_line, chunk.end_line, chunk.symbol, chunk.kind, chunk.text),
                )
                self._connection.execute(
                    "INSERT INTO chunks_fts(chunk_id, symbol, text) VALUES(?, ?, ?)",
                    (chunk.chunk_id, chunk.symbol or "", chunk.text),
                )
        self._matrix_cache = None

    def forget_file(self, path: str) -> None:
        with self._connection:
            self._delete_chunks_of(path)
            self._connection.execute("DELETE FROM files WHERE path = ?", (path,))
            self._connection.execute("DELETE FROM imports WHERE path = ?", (path,))
        self._matrix_cache = None

    def _delete_chunks_of(self, path: str) -> None:
        ids = [row["chunk_id"] for row in self._connection.execute("SELECT chunk_id FROM chunks WHERE path = ?", (path,))]
        for chunk_id in ids:
            self._connection.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
            self._connection.execute("DELETE FROM vectors WHERE chunk_id = ?", (chunk_id,))
        self._connection.execute("DELETE FROM chunks WHERE path = ?", (path,))

    def mark_scanned(self, moment: datetime) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT INTO index_meta(key, value) VALUES('last_scan_at', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (moment.isoformat(),),
            )

    # -- vectors ----------------------------------------------------------

    def store_vectors(self, vectors: dict[str, list[float]]) -> None:
        """Persist embeddings, normalised, so cosine similarity is a dot product."""
        with self._connection:
            for chunk_id, values in vectors.items():
                vector = np.asarray(values, dtype=np.float32)
                magnitude = float(np.linalg.norm(vector))
                if magnitude == 0.0:
                    continue
                payload = (vector / magnitude).astype(np.float32).tobytes()
                self._connection.execute(
                    "INSERT INTO vectors(chunk_id, embedding) VALUES(?, ?) ON CONFLICT(chunk_id) DO UPDATE SET embedding=excluded.embedding",
                    (chunk_id, payload),
                )
        self._matrix_cache = None

    def chunk_ids_without_vectors(self, *, limit: int) -> list[str]:
        rows = self._connection.execute(
            "SELECT chunks.chunk_id AS chunk_id FROM chunks LEFT JOIN vectors USING (chunk_id) WHERE vectors.chunk_id IS NULL LIMIT ?",
            (int(limit),),
        )
        return [row["chunk_id"] for row in rows]

    def search_vector(self, query_vector: list[float], *, limit: int) -> list[tuple[str, float]]:
        chunk_ids, matrix = self._vector_matrix()
        if not chunk_ids:
            return []
        query = np.asarray(query_vector, dtype=np.float32)
        magnitude = float(np.linalg.norm(query))
        if magnitude == 0.0 or query.shape[0] != matrix.shape[1]:
            return []
        scores = matrix @ (query / magnitude)
        count = min(int(limit), scores.shape[0])
        best = np.argpartition(-scores, count - 1)[:count] if count < scores.shape[0] else np.arange(scores.shape[0])
        ordered = best[np.argsort(-scores[best])]
        return [(chunk_ids[index], float(scores[index])) for index in ordered]

    def _vector_matrix(self) -> tuple[list[str], np.ndarray]:
        """The whole vector table as one matrix, cached until the next write.

        At the 50k-chunk ceiling with 768 dimensions this is about 150 MB of
        float32 — the reason the ceiling exists.
        """
        cached = self._matrix_cache
        if cached is not None:
            return cached  # type: ignore[return-value]
        chunk_ids: list[str] = []
        rows: list[np.ndarray] = []
        for row in self._connection.execute("SELECT chunk_id, embedding FROM vectors ORDER BY chunk_id"):
            values = np.frombuffer(row["embedding"], dtype=np.float32)
            if rows and values.shape[0] != rows[0].shape[0]:
                continue
            chunk_ids.append(row["chunk_id"])
            rows.append(values)
        matrix = np.vstack(rows) if rows else np.zeros((0, 0), dtype=np.float32)
        self._matrix_cache = (chunk_ids, matrix)
        return self._matrix_cache  # type: ignore[return-value]

    # -- import graph ---------------------------------------------------

    def replace_imports(self, path: str, targets: tuple[str, ...]) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM imports WHERE path = ?", (path,))
            for target in targets:
                self._connection.execute("INSERT OR IGNORE INTO imports(path, target) VALUES(?, ?)", (path, target))

    def neighbours_of(self, paths: list[str]) -> set[str]:
        """Files this set imports, plus files that import this set."""
        if not paths:
            return set()
        placeholders = ",".join("?" for _ in paths)
        outgoing = self._connection.execute(f"SELECT target FROM imports WHERE path IN ({placeholders})", paths)
        incoming = self._connection.execute(f"SELECT path FROM imports WHERE target IN ({placeholders})", paths)
        found = {row["target"] for row in outgoing} | {row["path"] for row in incoming}
        return found - set(paths)

    def most_imported(self, *, limit: int) -> list[tuple[str, int]]:
        rows = self._connection.execute(
            "SELECT target, COUNT(*) AS total FROM imports GROUP BY target ORDER BY total DESC, target ASC LIMIT ?",
            (int(limit),),
        )
        return [(row["target"], int(row["total"])) for row in rows]

    def symbols_of(self, path: str, *, limit: int) -> list[str]:
        rows = self._connection.execute(
            "SELECT DISTINCT symbol FROM chunks WHERE path = ? AND symbol IS NOT NULL ORDER BY start_line LIMIT ?",
            (path, int(limit)),
        )
        return [row["symbol"] for row in rows]

    # -- reads ----------------------------------------------------------

    def known_files(self) -> dict[str, tuple[str, int]]:
        """Every indexed path mapped to its content hash and mtime."""
        return {row["path"]: (row["content_hash"], row["mtime_ns"]) for row in self._connection.execute("SELECT path, content_hash, mtime_ns FROM files")}

    def chunks_by_id(self, chunk_ids: list[str]) -> dict[str, Chunk]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self._connection.execute(f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})", chunk_ids)
        found = {
            row["chunk_id"]: Chunk(
                path=row["path"], start_line=row["start_line"], end_line=row["end_line"],
                symbol=row["symbol"], kind=row["kind"], text=row["text"],
            )
            for row in rows
        }
        return {chunk_id: found[chunk_id] for chunk_id in chunk_ids if chunk_id in found}

    def search_lexical(self, query: str, *, limit: int) -> list[tuple[str, float]]:
        """Chunk ids ranked by BM25, best first. SQLite's bm25() is more negative for better matches."""
        expression = fts_query(query)
        if not expression:
            return []
        rows = self._connection.execute(
            "SELECT chunk_id, bm25(chunks_fts) AS rank FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
            (expression, int(limit)),
        )
        return [(row["chunk_id"], float(row["rank"])) for row in rows]

    def status(self) -> IndexStatus:
        files = self._connection.execute("SELECT COUNT(*) AS total FROM files").fetchone()["total"]
        chunks = self._connection.execute("SELECT COUNT(*) AS total FROM chunks").fetchone()["total"]
        vectors = self._connection.execute("SELECT COUNT(*) AS total FROM vectors").fetchone()["total"]
        row = self._connection.execute("SELECT value FROM index_meta WHERE key = 'last_scan_at'").fetchone()
        moment = datetime.fromisoformat(row["value"]) if row else None
        return IndexStatus(files=int(files), chunks=int(chunks), vectors=int(vectors), last_scan_at=moment)

    def close(self) -> None:
        self._connection.close()


__all__ = ["MAX_QUERY_TOKENS", "SCHEMA_VERSION", "SqliteChunkStore", "fts_query"]
