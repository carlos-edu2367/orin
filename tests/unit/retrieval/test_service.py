from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest

from agentos.retrieval.chunking import HeuristicChunker
from agentos.retrieval.indexer import ProjectIndexer
from agentos.retrieval.models import EmbedderIdentity
from agentos.retrieval.ports import EmbeddingUnavailable
from agentos.retrieval.service import GRAPH_BONUS, RetrievalService, reciprocal_rank_fusion
from agentos.retrieval.store import SqliteChunkStore


class KeywordEmbedder:
    """A deterministic stand-in: each dimension counts one keyword."""

    KEYWORDS = ("authorize", "policy", "colour", "browser")

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="fake", model="test", dim=len(self.KEYWORDS))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(text.lower().count(word)) + 0.01 for word in self.KEYWORDS] for text in texts]


def _service(tmp_path: Path, embedder=None) -> tuple[RetrievalService, ProjectIndexer]:
    embedder = embedder or KeywordEmbedder()
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    store = SqliteChunkStore(tmp_path / "index.db", embedder.identity)
    indexer = ProjectIndexer(root=root, store=store, chunker=HeuristicChunker(), embedder=embedder)
    return RetrievalService(store=store, indexer=indexer, embedder=embedder), indexer


def test_rrf_rewards_agreement_between_rankings_over_a_single_strong_ranking() -> None:
    # "b" appears in both rankings (rank 2, then rank 1); "a" only leads the
    # first. 1/(k+r) is convex, so two extreme ranks with the same mean would
    # actually outscore two moderate ones — the real, robust RRF property is
    # that presence in both rankings beats a lone rank-1 showing.
    scores = reciprocal_rank_fusion([["a", "b"], ["b"]])

    assert scores["b"] > scores["a"]


def test_search_returns_citable_hits(tmp_path: Path) -> None:
    service, indexer = _service(tmp_path)
    (indexer.root / "policy.py").write_text("def authorize():\n    return 'policy check'\n", encoding="utf-8")
    indexer.scan()

    result = service.search("authorize policy", limit=5)

    assert result.mode == "semantic"
    assert result.hits
    assert result.hits[0].path == "policy.py"
    assert result.hits[0].location.startswith("policy.py:")
    assert result.hits[0].symbol == "authorize"


def test_an_unavailable_embedder_degrades_to_lexical_and_says_so(tmp_path: Path) -> None:
    class BrokenEmbedder(KeywordEmbedder):
        def embed(self, texts):
            raise EmbeddingUnavailable("down")

    service, indexer = _service(tmp_path, BrokenEmbedder())
    (indexer.root / "policy.py").write_text("def authorize():\n    return 'policy check'\n", encoding="utf-8")
    indexer.scan()

    result = service.search("authorize", limit=5)

    assert result.mode == "lexical"
    assert result.hits
    assert result.hits[0].path == "policy.py"


def test_a_neighbour_in_the_import_graph_is_promoted(tmp_path: Path) -> None:
    service, indexer = _service(tmp_path)
    (indexer.root / "policy.py").write_text("def authorize():\n    return 'policy policy policy'\n", encoding="utf-8")
    (indexer.root / "caller.py").write_text("from policy import authorize\n\ndef run():\n    return authorize()\n", encoding="utf-8")
    (indexer.root / "lonely.py").write_text("def run():\n    return authorize\n", encoding="utf-8")
    indexer.scan()

    result = service.search("authorize policy", limit=10)
    ranked = [hit.path for hit in result.hits]

    assert ranked.index("caller.py") < ranked.index("lonely.py")


def test_an_empty_index_returns_no_hits_rather_than_raising(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    result = service.search("anything", limit=5)

    assert result.hits == []


def test_a_blank_query_is_refused(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    with pytest.raises(ValueError):
        service.search("   ", limit=5)


def test_the_graph_bonus_is_small_enough_not_to_outrank_a_top_result() -> None:
    # Rank 1 in both rankings scores 2/61 ≈ 0.0328; the bonus must stay well under it.
    assert 0 < GRAPH_BONUS < 1.0 / 61
