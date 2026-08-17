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
