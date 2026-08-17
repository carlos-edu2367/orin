from __future__ import annotations

from pathlib import Path

from agentos.retrieval.models import Chunk, EmbedderIdentity
from agentos.retrieval.store import SqliteChunkStore


def _chunk(start: int, text: str) -> Chunk:
    return Chunk(path="src/a.py", start_line=start, end_line=start + 1, symbol=None, kind="block", text=text)


def _seed(store: SqliteChunkStore) -> None:
    store.replace_file("src/a.py", content_hash="h", size_bytes=1, mtime_ns=1, language="python", chunks=[_chunk(1, "alpha"), _chunk(5, "beta")])


def test_vector_search_ranks_by_cosine_similarity(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", EmbedderIdentity("fake", "test", 3))
    _seed(store)
    store.store_vectors({"src/a.py:1-2": [1.0, 0.0, 0.0], "src/a.py:5-6": [0.0, 1.0, 0.0]})

    results = store.search_vector([0.9, 0.1, 0.0], limit=2)

    assert [chunk_id for chunk_id, _ in results] == ["src/a.py:1-2", "src/a.py:5-6"]
    assert results[0][1] > results[1][1]


def test_vectors_are_normalised_so_magnitude_does_not_change_the_ranking(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", EmbedderIdentity("fake", "test", 3))
    _seed(store)
    store.store_vectors({"src/a.py:1-2": [100.0, 0.0, 0.0], "src/a.py:5-6": [0.0, 1.0, 0.0]})

    results = store.search_vector([1.0, 0.0, 0.0], limit=1)

    assert results[0][0] == "src/a.py:1-2"
    assert results[0][1] == 1.0


def test_chunks_missing_a_vector_are_reported_for_embedding(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", EmbedderIdentity("fake", "test", 3))
    _seed(store)
    store.store_vectors({"src/a.py:1-2": [1.0, 0.0, 0.0]})

    assert store.chunk_ids_without_vectors(limit=10) == ["src/a.py:5-6"]


def test_reopening_with_a_different_model_discards_the_vectors_but_keeps_the_text(tmp_path: Path) -> None:
    database = tmp_path / "index.db"
    first = SqliteChunkStore(database, EmbedderIdentity("fake", "test", 3))
    _seed(first)
    first.store_vectors({"src/a.py:1-2": [1.0, 0.0, 0.0]})
    first.close()

    second = SqliteChunkStore(database, EmbedderIdentity("fake", "other-model", 3))

    assert second.status().vectors == 0
    assert second.status().chunks == 2
    assert second.search_lexical("alpha", limit=5)


def test_reopening_with_the_same_identity_keeps_the_vectors(tmp_path: Path) -> None:
    database = tmp_path / "index.db"
    identity = EmbedderIdentity("fake", "test", 3)
    first = SqliteChunkStore(database, identity)
    _seed(first)
    first.store_vectors({"src/a.py:1-2": [1.0, 0.0, 0.0]})
    first.close()

    assert SqliteChunkStore(database, identity).status().vectors == 1
