"""Query-time freshness verification for code retrieval results (D-RF1).

Before ``RetrievalService.search`` returns hits, the files backing the top
``limit`` hits are stat-checked against ``files(mtime_ns, size_bytes)``:

- Missing files are forgotten immediately and their hits are dropped.
- Intact files pass through without reindexing.
- Divergent files are reindexed inline while within ``budget_ms`` (default
  200 ms), then re-evaluated so stale snippets are never returned.
- When the budget is exhausted (or an active background scan holds the lock),
  stale hits are discarded and reindexing is scheduled asynchronously.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import threading
import time

from agentos.pathsafety import resolve_contained

from .filters import normalize_rel_path
from .indexer import ProjectIndexer
from .models import SearchHit
from .store import SqliteChunkStore


DEFAULT_FRESHNESS_BUDGET_MS = 200.0
_MAX_HIT_CHARS = 2_000

ReevaluateFn = Callable[[set[str], set[str], set[str]], list[SearchHit]]


class FreshnessChecker:
    """Verify disk freshness for the files referenced by top search hits."""

    def __init__(
        self,
        *,
        store: SqliteChunkStore,
        indexer: ProjectIndexer,
        workspace_root: Path | None = None,
        schedule_reindex: Callable[[list[str]], None] | None = None,
        scan_lock: threading.Lock | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._indexer = indexer
        root = workspace_root if workspace_root is not None else indexer.root
        self._root = Path(root).resolve()
        self._schedule_reindex = schedule_reindex
        self._scan_lock = scan_lock
        self._clock = clock

    def filter(
        self,
        results: Sequence[SearchHit],
        limit: int,
        budget_ms: float = DEFAULT_FRESHNESS_BUDGET_MS,
        *,
        reevaluate: ReevaluateFn | None = None,
        max_hit_chars: int = _MAX_HIT_CHARS,
    ) -> list[SearchHit]:
        """Return only hits backed by fresh files, reindexing divergent ones within ``budget_ms``."""
        effective_limit = int(limit)
        if effective_limit <= 0 or not results:
            return []

        window = list(results[:effective_limit])
        started = self._clock()
        budget_limit = float(budget_ms)

        kept_paths: set[str] = set()
        reindexed_paths: set[str] = set()
        discarded_paths: set[str] = set()
        deferred_paths: list[str] = []

        unique_paths: list[str] = []
        seen: set[str] = set()
        for hit in window:
            norm_path = normalize_rel_path(hit.path)
            if norm_path not in seen:
                seen.add(norm_path)
                unique_paths.append(norm_path)

        for rel_path in unique_paths:
            resolved = resolve_contained(self._root / rel_path, self._root)
            if resolved is None or not resolved.is_file():
                self._store.forget_file(rel_path)
                discarded_paths.add(rel_path)
                continue

            try:
                stat = resolved.stat()
            except OSError:
                self._store.forget_file(rel_path)
                discarded_paths.add(rel_path)
                continue

            recorded = self._store.file_state(rel_path)
            if recorded is None:
                discarded_paths.add(rel_path)
                continue

            if recorded == (stat.st_mtime_ns, stat.st_size):
                kept_paths.add(rel_path)
                continue

            elapsed_ms = (self._clock() - started) * 1000.0
            if budget_limit <= 0.0 or elapsed_ms >= budget_limit:
                discarded_paths.add(rel_path)
                deferred_paths.append(rel_path)
                continue

            outcome = self._reindex_inline(rel_path)
            if outcome == "updated":
                reindexed_paths.add(rel_path)
            elif outcome == "unchanged":
                kept_paths.add(rel_path)
            elif outcome == "forgotten":
                discarded_paths.add(rel_path)
            else:
                discarded_paths.add(rel_path)
                deferred_paths.append(rel_path)

        if deferred_paths and self._schedule_reindex is not None:
            try:
                self._schedule_reindex(deferred_paths)
            except Exception:  # noqa: BLE001 - scheduling must never fail a search call
                pass

        if not reindexed_paths:
            return [hit for hit in window if normalize_rel_path(hit.path) in kept_paths]

        if reevaluate is not None:
            return reevaluate(kept_paths, reindexed_paths, discarded_paths)[:effective_limit]

        return self._replace_reindexed_hits(window, kept_paths, reindexed_paths, effective_limit, max_hit_chars)

    def _reindex_inline(self, rel_path: str) -> str:
        lock = self._scan_lock
        if lock is not None:
            if not lock.acquire(blocking=False):
                return "busy"
            try:
                return self._indexer.index_file(rel_path, embed=True)
            except Exception:  # noqa: BLE001
                return "error"
            finally:
                lock.release()
        try:
            return self._indexer.index_file(rel_path, embed=True)
        except Exception:  # noqa: BLE001
            return "error"

    def _replace_reindexed_hits(
        self,
        window: list[SearchHit],
        kept_paths: set[str],
        reindexed_paths: set[str],
        limit: int,
        max_hit_chars: int,
    ) -> list[SearchHit]:
        fresh_hits: list[SearchHit] = []
        emitted_reindexed: set[str] = set()
        for hit in window:
            norm_path = normalize_rel_path(hit.path)
            if norm_path in kept_paths:
                fresh_hits.append(hit)
            elif norm_path in reindexed_paths and norm_path not in emitted_reindexed:
                emitted_reindexed.add(norm_path)
                for chunk in self._store.chunks_of_file(norm_path):
                    fresh_hits.append(
                        SearchHit(
                            path=chunk.path,
                            start_line=chunk.start_line,
                            end_line=chunk.end_line,
                            symbol=chunk.symbol,
                            score=hit.score,
                            text=chunk.text[:max_hit_chars],
                        )
                    )
                    if len(fresh_hits) >= limit:
                        return fresh_hits[:limit]
            if len(fresh_hits) >= limit:
                return fresh_hits[:limit]
        return fresh_hits[:limit]


__all__ = ["DEFAULT_FRESHNESS_BUDGET_MS", "FreshnessChecker", "ReevaluateFn"]
