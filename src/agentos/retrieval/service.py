"""Hybrid retrieval: vectors and BM25, fused, then nudged by the import graph.

Cosine scores and BM25 scores are not comparable on any shared scale, so they
are never added. Reciprocal Rank Fusion uses only the position of a chunk in
each ranking, which makes the combination robust without any tuning.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import threading
import time
from typing import Callable

from .indexer import ProjectIndexer
from .models import IndexStatus, SearchHit
from .pagerank import personalized_pagerank
from .ports import EmbeddingPort, EmbeddingUnavailable
from .store import SqliteChunkStore


CANDIDATES = 50
RRF_K = 60
GRAPH_BONUS = 0.005
GRAPH_SEEDS = 5
STALE_AFTER = timedelta(seconds=60)
MAX_HIT_CHARS = 2_000
MAX_MAP_SYMBOLS = 12
DEFAULT_MAP_TOKENS = 1_200
MAP_TOKEN_TOLERANCE = 0.15


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: list[SearchHit]
    mode: str
    status: IndexStatus


def reciprocal_rank_fusion(rankings: list[list[str]], *, k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + position)
    return scores


class RetrievalService:
    def __init__(self, *, store: SqliteChunkStore, indexer: ProjectIndexer, embedder: EmbeddingPort) -> None:
        self._store = store
        self._indexer = indexer
        self._embedder = embedder
        self._last_scan_embedded = True
        # One scan at a time. A tool call never waits on it: the first scan of
        # a large project takes minutes, and a turn blocked on it looks hung.
        self._scan_lock = threading.Lock()
        self._background_refresh: Callable[[], None] | None = None
        self._refresh_requested_at: float | None = None

    def attach_background_refresh(self, schedule: Callable[[], None]) -> None:
        """Hand stale-index refreshes to the background worker instead of the tool call."""
        self._background_refresh = schedule

    @property
    def indexing(self) -> bool:
        """True while a scan runs or before the first one has ever finished."""
        return self._scan_lock.locked() or self._store.status().last_scan_at is None

    def search(self, query: str, *, limit: int = 8) -> SearchResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-blank string")
        self._refresh_if_stale(embed=True)
        lexical = self._store.search_lexical(query, limit=CANDIDATES)
        rankings = [[chunk_id for chunk_id, _ in lexical]]
        mode = "lexical"
        try:
            vectors = self._embedder.embed([query])
            if vectors:
                vector = vectors[0]
                semantic = self._store.search_vector(vector, limit=CANDIDATES)
                if semantic:
                    rankings.append([chunk_id for chunk_id, _ in semantic])
                    mode = "semantic"
        except (EmbeddingUnavailable, Exception):
            pass
        scores = reciprocal_rank_fusion(rankings)
        if not scores:
            return SearchResult(hits=[], mode=mode, status=self._store.status())
        ordered = sorted(scores, key=lambda chunk_id: -scores[chunk_id])
        scores = self._apply_graph_bonus(ordered, scores)
        ordered = sorted(scores, key=lambda chunk_id: -scores[chunk_id])[: max(1, int(limit))]
        chunks = self._store.chunks_by_id(ordered)
        hits = [
            SearchHit(
                path=chunk.path, start_line=chunk.start_line, end_line=chunk.end_line,
                symbol=chunk.symbol, score=round(scores[chunk_id], 6), text=chunk.text[:MAX_HIT_CHARS],
            )
            for chunk_id, chunk in chunks.items()
        ]
        return SearchResult(hits=hits, mode=mode, status=self._store.status())

    def _apply_graph_bonus(self, ordered: list[str], scores: dict[str, float]) -> dict[str, float]:
        seeds = {chunk_id.rsplit(":", 1)[0] for chunk_id in ordered[:GRAPH_SEEDS]}
        neighbours = self._store.neighbours_of(sorted(seeds))
        if not neighbours:
            return scores
        return {
            chunk_id: value + (GRAPH_BONUS if chunk_id.rsplit(":", 1)[0] in neighbours else 0.0)
            for chunk_id, value in scores.items()
        }

    def _refresh_if_stale(self, *, embed: bool = True) -> None:
        """Cover edits made outside Orin, without paying for a scan every call.

        Never blocks on a scan. With a background worker attached the refresh
        is queued and the caller answers from the index as it stands; without
        one (tests, one-off use) the scan runs inline unless another one is
        already running, in which case the caller also answers right away.
        """
        last = self._store.status().last_scan_at
        fresh = last is not None and datetime.now(UTC) - last < STALE_AFTER
        if fresh and (not embed or self._last_scan_embedded):
            return
        if self._background_refresh is not None:
            self._schedule_background_refresh()
            return
        if not self._scan_lock.acquire(blocking=False):
            return
        try:
            self._indexer.scan(embed=embed)
            self._last_scan_embedded = bool(embed)
        except OSError:
            return
        finally:
            self._scan_lock.release()

    def _schedule_background_refresh(self) -> None:
        if self._scan_lock.locked():
            return
        now = time.monotonic()
        requested = self._refresh_requested_at
        if requested is not None and now - requested < STALE_AFTER.total_seconds():
            return
        self._refresh_requested_at = now
        try:
            self._background_refresh()  # type: ignore[misc]
        except Exception:  # noqa: BLE001 - a refresh request never fails a tool call
            self._refresh_requested_at = None

    def reindex(self, paths: list[str] | None = None, *, embed: bool = True) -> None:
        with self._scan_lock:
            if paths is None:
                self._refresh_requested_at = None
            self._indexer.scan(only=paths, embed=embed)
            if embed:
                self._last_scan_embedded = True

    def status(self) -> IndexStatus:
        return self._store.status()

    def close(self) -> None:
        self._store.close()

    def _relativize_focus_paths(self, focus_paths: Sequence[str], all_paths: Sequence[str]) -> tuple[str, ...]:
        if not focus_paths:
            return ()
        root_path = getattr(self._indexer, "root", None)
        root_posix = Path(root_path).resolve().as_posix().rstrip("/") if root_path is not None else ""
        root_lower = root_posix.lower()
        known_set = set(all_paths)
        lower_map = {p.lower(): p for p in all_paths}
        normalized: list[str] = []
        seen: set[str] = set()

        for raw in focus_paths:
            if not isinstance(raw, str) or not raw.strip():
                continue
            cleaned = raw.strip().replace("\\", "/")
            is_abs = cleaned.startswith("/") or (len(cleaned) >= 2 and cleaned[1] == ":")
            rel: str | None = None
            if root_posix:
                if cleaned.lower() == root_lower:
                    continue
                if cleaned.lower().startswith(root_lower + "/"):
                    rel = cleaned[len(root_posix) + 1 :].lstrip("/")
                elif is_abs:
                    try:
                        resolved_posix = Path(raw).resolve().as_posix()
                    except OSError:
                        resolved_posix = cleaned
                    if resolved_posix.lower().startswith(root_lower + "/"):
                        rel = resolved_posix[len(root_posix) + 1 :].lstrip("/")
                    else:
                        continue
            if rel is None:
                if is_abs and root_posix:
                    continue
                rel = cleaned
                while rel.startswith("./"):
                    rel = rel[2:]
                rel = rel.lstrip("/")
            if not rel:
                continue
            canonical = rel if rel in known_set else lower_map.get(rel.lower(), rel)
            if canonical not in seen:
                seen.add(canonical)
                normalized.append(canonical)
        return tuple(normalized)

    def project_map(
        self,
        *,
        limit: int = 20,
        focus_paths: Sequence[str] = (),
        query: str = "",
        max_tokens: int = DEFAULT_MAP_TOKENS,
    ) -> list[dict[str, object]]:
        """The files most relevant to the current task, ranked by Personalized PageRank.

        Uses binary search (ok_err = 0.15) over the rendered map size to fit the
        richest possible file/symbol summary inside ``max_tokens``.
        """
        self._refresh_if_stale(embed=False)
        known_paths = set(self._store.known_files())
        imported_by = self._store.imported_by_counts()
        edges = self._store.dependency_edges()
        all_paths = sorted(
            known_paths
            | set(imported_by)
            | {src for src, _, _, _ in edges}
            | {dst for _, dst, _, _ in edges}
        )
        if not all_paths:
            return []

        rel_focus_paths = self._relativize_focus_paths(focus_paths, all_paths)
        file_symbols = self._store.symbols_by_file()
        scores = personalized_pagerank(
            all_paths,
            edges,
            file_symbols=file_symbols,
            focus_paths=rel_focus_paths,
            query=query,
        )
        out_degree: dict[str, int] = {}
        for src_path, _dst_path, _weight, _syms in edges:
            out_degree[src_path] = out_degree.get(src_path, 0) + 1

        ranked_paths = sorted(
            all_paths,
            key=lambda path: (
                -round(scores.get(path, 0.0), 9),
                -imported_by.get(path, 0),
                -out_degree.get(path, 0),
                path,
            ),
        )

        capped_limit = max(1, int(limit))
        target_tokens = max(1, int(max_tokens))
        # When default limit=20 is used with a larger token budget, allow expanding
        # the candidate pool so the binary search can fill max_tokens if needed.
        pool_limit = (
            min(len(ranked_paths), max(capped_limit, target_tokens // 12))
            if int(limit) == 20
            else min(len(ranked_paths), capped_limit)
        )
        candidate_paths = ranked_paths[:pool_limit]
        symbol_lines_by_path: dict[str, list[tuple[str, int]]] = {
            path: self._store.symbols_with_lines_of(path, limit=max(MAX_MAP_SYMBOLS, 48))
            for path in candidate_paths
        }

        return _fit_project_map_to_budget(
            candidate_paths=candidate_paths,
            primary_limit=min(len(candidate_paths), capped_limit),
            symbol_lines_by_path=symbol_lines_by_path,
            imported_by=imported_by,
            scores=scores,
            max_tokens=target_tokens,
            ok_err=MAP_TOKEN_TOLERANCE,
        )


def format_project_map_entries(entries: Sequence[Mapping[str, object]]) -> str:
    """Render project map entries with ``symbol:start_line`` locations."""
    if not entries:
        return "[the project index is empty]"
    lines: list[str] = []
    for entry in entries:
        formatted = entry.get("formatted_symbols")
        if isinstance(formatted, Sequence) and not isinstance(formatted, (str, bytes)):
            sym_text = ", ".join(str(item) for item in formatted)
        elif isinstance(entry.get("symbol_lines"), Sequence):
            sym_text = ", ".join(
                f"{sym}:{line}" for sym, line in entry["symbol_lines"]  # type: ignore[index]
            )
        else:
            syms = entry.get("symbols") or ()
            sym_text = ", ".join(str(item) for item in syms) if isinstance(syms, Sequence) else ""
        lines.append(f"{entry['path']} (imported by {entry['imported_by']}): {sym_text or '—'}")
    return "\n".join(lines)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _fit_project_map_to_budget(
    *,
    candidate_paths: list[str],
    primary_limit: int,
    symbol_lines_by_path: dict[str, list[tuple[str, int]]],
    imported_by: dict[str, int],
    scores: dict[str, float],
    max_tokens: int,
    ok_err: float = MAP_TOKEN_TOLERANCE,
) -> list[dict[str, object]]:
    if not candidate_paths:
        return []

    # Build a monotonic progressive schedule of states. Each state is a tuple of
    # (num_files, sym_counts_per_file) so binary search can smoothly adjust both
    # the number of files and symbols per file to match max_tokens within ±ok_err.
    schedule: list[list[tuple[str, int]]] = []
    current_counts: dict[str, int] = {}
    ordered_active: list[str] = []

    # Phase 1: Add primary_limit files in PageRank order, growing each file's
    # symbols up to MAX_MAP_SYMBOLS one by one.
    for path in candidate_paths[:primary_limit]:
        ordered_active.append(path)
        syms = symbol_lines_by_path.get(path, [])
        cap = min(len(syms), MAX_MAP_SYMBOLS)
        if cap == 0:
            current_counts[path] = 0
            schedule.append([(p, current_counts[p]) for p in ordered_active])
        else:
            for count in range(1, cap + 1):
                current_counts[path] = count
                schedule.append([(p, current_counts[p]) for p in ordered_active])

    # Phase 2: Additional symbols beyond MAX_MAP_SYMBOLS for primary_limit files
    max_extra = max((len(symbol_lines_by_path.get(p, [])) for p in ordered_active), default=0)
    for count in range(MAX_MAP_SYMBOLS + 1, max_extra + 1):
        for path in ordered_active:
            if len(symbol_lines_by_path.get(path, [])) >= count:
                current_counts[path] = count
                schedule.append([(p, current_counts[p]) for p in ordered_active])

    # Phase 3: Extra candidate files beyond primary_limit if pool was expanded
    for path in candidate_paths[primary_limit:]:
        ordered_active.append(path)
        syms = symbol_lines_by_path.get(path, [])
        cap = min(len(syms), MAX_MAP_SYMBOLS)
        if cap == 0:
            current_counts[path] = 0
            schedule.append([(p, current_counts[p]) for p in ordered_active])
        else:
            for count in range(1, cap + 1):
                current_counts[path] = count
                schedule.append([(p, current_counts[p]) for p in ordered_active])

    def _build_entries(step_idx: int) -> list[dict[str, object]]:
        alloc = schedule[step_idx]
        entries: list[dict[str, object]] = []
        for path, sym_count in alloc:
            sym_lines = symbol_lines_by_path.get(path, [])[:sym_count]
            entries.append(
                {
                    "path": path,
                    "imported_by": int(imported_by.get(path, 0)),
                    "symbols": [sym for sym, _ in sym_lines],
                    "symbol_lines": list(sym_lines),
                    "formatted_symbols": [f"{sym}:{line}" for sym, line in sym_lines],
                    "score": round(float(scores.get(path, 0.0)), 6),
                }
            )
        return entries

    def _tokens_at(step_idx: int) -> int:
        return _estimate_tokens(format_project_map_entries(_build_entries(step_idx)))

    # If the full primary schedule (step at end of Phase 1) fits within max_tokens,
    # and we don't need Phase 2/3 to reach the lower bound, return Phase 1's end.
    phase1_end_idx = 0
    for idx, state in enumerate(schedule):
        if len(state) <= primary_limit and all(c <= MAX_MAP_SYMBOLS for _, c in state):
            phase1_end_idx = idx
        else:
            break

    low_bound = max_tokens * (1.0 - ok_err)
    high_bound = max_tokens * (1.0 + ok_err)

    phase1_tokens = _tokens_at(phase1_end_idx)
    if low_bound <= phase1_tokens <= high_bound:
        return _build_entries(phase1_end_idx)
    if phase1_tokens < low_bound and phase1_end_idx == len(schedule) - 1:
        return _build_entries(phase1_end_idx)

    total_tokens = _tokens_at(len(schedule) - 1)
    if total_tokens <= max_tokens:
        return _build_entries(len(schedule) - 1)

    low = 0
    high = len(schedule) - 1
    best_idx = 0
    best_diff = abs(_tokens_at(0) - max_tokens)

    while low <= high:
        mid = (low + high) // 2
        tokens = _tokens_at(mid)
        diff = abs(tokens - max_tokens)
        if tokens <= high_bound and (diff < best_diff or best_idx == 0):
            best_idx = mid
            best_diff = diff
        if low_bound <= tokens <= high_bound:
            best_idx = mid
            best_diff = diff
            if tokens < max_tokens:
                low = mid + 1
            else:
                break
        elif tokens < low_bound:
            best_idx = mid
            best_diff = diff
            low = mid + 1
        else:
            high = mid - 1

    return _build_entries(best_idx)


__all__ = [
    "CANDIDATES", "DEFAULT_MAP_TOKENS", "GRAPH_BONUS", "GRAPH_SEEDS",
    "MAP_TOKEN_TOLERANCE", "MAX_MAP_SYMBOLS", "RRF_K", "RetrievalService",
    "SearchResult", "format_project_map_entries", "reciprocal_rank_fusion",
]
