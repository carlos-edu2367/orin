"""Hybrid retrieval: vectors and BM25, fused, then nudged by the import graph.

Cosine scores and BM25 scores are not comparable on any shared scale, so they
are never added. Reciprocal Rank Fusion uses only the position of a chunk in
each ranking, which makes the combination robust without any tuning.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .indexer import ProjectIndexer
from .models import IndexStatus, SearchHit
from .ports import EmbeddingPort, EmbeddingUnavailable
from .store import SqliteChunkStore


CANDIDATES = 50
RRF_K = 60
GRAPH_BONUS = 0.005
GRAPH_SEEDS = 5
STALE_AFTER = timedelta(seconds=60)
MAX_HIT_CHARS = 2_000
MAX_MAP_SYMBOLS = 12


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

    def search(self, query: str, *, limit: int = 8) -> SearchResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-blank string")
        self._refresh_if_stale()
        lexical = self._store.search_lexical(query, limit=CANDIDATES)
        rankings = [[chunk_id for chunk_id, _ in lexical]]
        mode = "lexical"
        try:
            vector = self._embedder.embed([query])[0]
            semantic = self._store.search_vector(vector, limit=CANDIDATES)
            if semantic:
                rankings.append([chunk_id for chunk_id, _ in semantic])
                mode = "semantic"
        except EmbeddingUnavailable:
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

    def _refresh_if_stale(self) -> None:
        """Cover edits made outside Orin, without paying for a scan every call."""
        last = self._store.status().last_scan_at
        if last is not None and datetime.now(UTC) - last < STALE_AFTER:
            return
        try:
            self._indexer.scan()
        except OSError:
            return

    def reindex(self, paths: list[str] | None = None) -> None:
        self._indexer.scan(only=paths)

    def status(self) -> IndexStatus:
        return self._store.status()

    def close(self) -> None:
        self._store.close()

    def project_map(self, *, limit: int = 20) -> list[dict[str, object]]:
        """The files most depended on, with their top-level symbols.

        Everything here is already in the index; this is a projection, not a
        second analysis.
        """
        self._refresh_if_stale()
        return [
            {"path": path, "imported_by": count, "symbols": self._store.symbols_of(path, limit=MAX_MAP_SYMBOLS)}
            for path, count in self._store.most_imported(limit=max(1, int(limit)))
        ]


__all__ = [
    "CANDIDATES", "GRAPH_BONUS", "GRAPH_SEEDS", "MAX_MAP_SYMBOLS", "RRF_K",
    "RetrievalService", "SearchResult", "reciprocal_rank_fusion",
]
