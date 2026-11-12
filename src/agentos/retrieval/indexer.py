"""Walk the project and keep the index in step with the disk.

Everything here is incremental. ``mtime_ns`` and size together decide whether
a file is even read; the content hash decides whether it is re-embedded.
Embedding is the expensive step and is the last thing attempted, so an
embedder outage costs vectors but never costs the lexical index.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import blake2b
from pathlib import Path
import re

from agentos.pathsafety import resolve_contained

from .filters import GitignoreFilter, IndexFilter
from .ports import Chunker, EmbeddingPort, EmbeddingUnavailable
from .store import SqliteChunkStore
from .symbols import extract_imports, language_for, resolve_import


MAX_FILE_BYTES = 2_000_000
MAX_CHUNKS = 50_000
EMBED_BATCH = 64
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ProjectIndexer:
    def __init__(self, *, root: Path, store: SqliteChunkStore, chunker: Chunker, embedder: EmbeddingPort) -> None:
        self.root = Path(root).resolve()
        self._store = store
        self._chunker = chunker
        self._embedder = embedder

    # -- scanning -------------------------------------------------------

    def scan(self, only: list[str] | None = None, *, embed: bool = True) -> None:
        """Reconcile the index with the disk, optionally for a subset of paths."""
        index_filter = IndexFilter(GitignoreFilter.from_root(self.root))
        known = self._store.known_files()
        prior_symbols = self._store.defined_symbols() if known else set()
        present: set[str] = set()
        pending_imports: dict[str, tuple[str | None, str]] = {}
        for path in self._candidates(index_filter, only):
            present.add(path)
            outcome = self._index_one(path, known)
            if outcome is not None:
                pending_imports[path] = outcome
        removed = (set(known) - present) if only is None else {path for path in only if path not in present and path in known}
        for path in removed:
            self._store.forget_file(path)
        # Imports and symbol references are resolved only after every file in
        # this scan has been written, so a file that references one indexed
        # later in the same walk still resolves against the full symbol set.
        if pending_imports:
            known_paths = tuple(self._store.known_files())
            known_symbols = self._store.defined_symbols()
            defs_by_file = self._store.symbols_by_file()
            for path, (language, text) in pending_imports.items():
                self._record_imports(path, language, text, known_paths)
                self._record_symbol_refs(path, text, known_symbols, defs_by_file.get(path, ()))
            added_symbols = known_symbols - prior_symbols
            if added_symbols and len(pending_imports) < len(known_paths):
                candidate_other_paths = (
                    self._store.paths_mentioning_tokens(added_symbols)
                    if hasattr(self._store, "paths_mentioning_tokens")
                    else set(known_paths)
                )
                for other_path in known_paths:
                    if other_path in pending_imports or other_path not in candidate_other_paths:
                        continue
                    resolved = self._resolve(other_path)
                    if resolved is None:
                        continue
                    try:
                        other_text = resolved.read_bytes().decode("utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    if any(sym in other_text for sym in added_symbols):
                        self._record_symbol_refs(other_path, other_text, known_symbols, defs_by_file.get(other_path, ()))
        if embed:
            self._embed_pending()
        self._store.mark_scanned(datetime.now(UTC))

    def _candidates(self, index_filter: IndexFilter, only: list[str] | None) -> list[str]:
        if only is not None:
            return [path for path in only if not index_filter.rejects(path) and self._resolve(path) is not None]
        found: list[str] = []
        for item in self.root.rglob("*"):
            resolved = self._resolve_absolute(item)
            if resolved is None:
                continue
            relative = resolved.relative_to(self.root).as_posix()
            if index_filter.rejects(relative):
                continue
            found.append(relative)
            if len(found) >= MAX_CHUNKS:
                break
        return found

    def _resolve(self, relative_path: str) -> Path | None:
        return self._resolve_absolute(self.root / relative_path)

    def _resolve_absolute(self, item: Path) -> Path | None:
        """Resolve via the shared sandbox guard, the same one the workspace search uses."""
        resolved = resolve_contained(item, self.root)
        if resolved is None:
            return None
        try:
            if not resolved.is_file() or resolved.stat().st_size > MAX_FILE_BYTES:
                return None
        except OSError:
            return None
        return resolved

    def _index_one(self, relative_path: str, known: dict[str, tuple[str, int, int]]) -> tuple[str | None, str] | None:
        """Index a file's content if it changed; return (language, text) for later import resolution."""
        resolved = self._resolve(relative_path)
        if resolved is None:
            return None
        try:
            stat = resolved.stat()
            previous = known.get(relative_path)
            # Size has to agree too, not just mtime: filesystem timestamp
            # granularity is coarse (~16ms on NTFS, and whole seconds on some
            # filesystems), so a file rewritten in the same tick it was
            # indexed keeps its mtime and would otherwise stay stale in the
            # index forever -- the content hash below is never even reached.
            if previous is not None and previous[1] == stat.st_mtime_ns and previous[2] == stat.st_size:
                return None
            payload = resolved.read_bytes()
        except OSError:
            return None
        content_hash = blake2b(payload, digest_size=16).hexdigest()
        if previous is not None and previous[0] == content_hash:
            return None
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
        language = language_for(relative_path)
        chunks = self._chunker.split(relative_path, text)
        self._store.replace_file(
            relative_path, content_hash=content_hash, size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns, language=language, chunks=chunks,
        )
        return (language, text)

    def _record_imports(self, relative_path: str, language: str | None, text: str, known_paths: tuple[str, ...]) -> None:
        raw = extract_imports(language, text)
        if not raw:
            self._store.replace_imports(relative_path, ())
            return
        resolved = tuple(
            target for target in (resolve_import(language, item, relative_path, known_paths) for item in raw)
            if target is not None and target != relative_path
        )
        self._store.replace_imports(relative_path, resolved)

    def _record_symbol_refs(
        self,
        relative_path: str,
        text: str,
        known_symbols: set[str],
        own_symbols: Sequence[str],
    ) -> None:
        if not known_symbols or not text:
            self._store.replace_symbol_refs(relative_path, {})
            return
        counts = Counter(_IDENTIFIER_RE.findall(text))
        for own_sym in own_symbols:
            if own_sym in counts:
                counts[own_sym] -= 1
        refs = {
            sym: count
            for sym, count in counts.items()
            if sym in known_symbols and count > 0
        }
        self._store.replace_symbol_refs(relative_path, refs)

    # -- embedding ------------------------------------------------------

    def _embed_pending(self) -> None:
        """Fill in missing vectors, in batches, stopping at the first refusal."""
        while True:
            pending = self._store.chunk_ids_without_vectors(limit=EMBED_BATCH)
            if not pending:
                return
            chunks = self._store.chunks_by_id(pending)
            texts = [chunk.text for chunk in chunks.values()]
            if not texts:
                return
            try:
                vectors = self._embedder.embed(texts)
            except Exception:
                return
            if not vectors:
                return
            self._store.store_vectors(dict(zip(chunks.keys(), vectors, strict=False)))
            if len(vectors) < len(chunks):
                return


WorkspaceIndexer = ProjectIndexer

__all__ = ["EMBED_BATCH", "MAX_CHUNKS", "MAX_FILE_BYTES", "ProjectIndexer", "WorkspaceIndexer"]
