from __future__ import annotations

from pathlib import Path
from typing import Sequence

from agentos.retrieval.chunking import HeuristicChunker
from agentos.retrieval.indexer import ProjectIndexer
from agentos.retrieval.models import EmbedderIdentity
from agentos.retrieval.store import SqliteChunkStore


class CountingEmbedder:
    """Deterministic vectors plus a call counter, so re-embedding is observable."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="fake", model="test", dim=4)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(text) % 7), 1.0, 0.0, 0.0] for text in texts]


def _indexer(tmp_path: Path, embedder: CountingEmbedder) -> tuple[ProjectIndexer, SqliteChunkStore]:
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    store = SqliteChunkStore(tmp_path / "index.db", embedder.identity)
    return ProjectIndexer(root=root, store=store, chunker=HeuristicChunker(), embedder=embedder), store


def test_a_full_scan_indexes_text_files_and_embeds_their_chunks(tmp_path: Path) -> None:
    embedder = CountingEmbedder()
    indexer, store = _indexer(tmp_path, embedder)
    (indexer.root / "a.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    indexer.scan()

    assert store.status().files == 1
    assert store.status().chunks >= 1
    assert store.status().vectors == store.status().chunks


def test_an_unchanged_file_is_not_re_embedded(tmp_path: Path) -> None:
    embedder = CountingEmbedder()
    indexer, _ = _indexer(tmp_path, embedder)
    (indexer.root / "a.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    indexer.scan()
    first = len(embedder.calls)

    indexer.scan()

    assert len(embedder.calls) == first


def test_changed_content_is_re_embedded(tmp_path: Path) -> None:
    embedder = CountingEmbedder()
    indexer, store = _indexer(tmp_path, embedder)
    target = indexer.root / "a.py"
    target.write_text("def run():\n    return 1\n", encoding="utf-8")
    indexer.scan()

    target.write_text("def run():\n    return 2\n\ndef extra():\n    return 3\n", encoding="utf-8")
    indexer.scan()

    assert store.status().chunks >= 2
    assert store.status().vectors == store.status().chunks


def test_a_deleted_file_is_forgotten(tmp_path: Path) -> None:
    embedder = CountingEmbedder()
    indexer, store = _indexer(tmp_path, embedder)
    target = indexer.root / "a.py"
    target.write_text("def run():\n    return 1\n", encoding="utf-8")
    indexer.scan()

    target.unlink()
    indexer.scan()

    assert store.status().files == 0
    assert store.status().chunks == 0


def test_denied_paths_and_secrets_are_never_read(tmp_path: Path) -> None:
    embedder = CountingEmbedder()
    indexer, store = _indexer(tmp_path, embedder)
    (indexer.root / ".env").write_text("OPENAI_API_KEY=super-secret\n", encoding="utf-8")
    (indexer.root / "node_modules").mkdir()
    (indexer.root / "node_modules" / "lib.js").write_text("export const a = 1;\n", encoding="utf-8")
    (indexer.root / "keep.py").write_text("value = 1\n", encoding="utf-8")

    indexer.scan()

    assert set(store.known_files()) == {"keep.py"}
    assert store.search_lexical("super", limit=5) == []


def test_gitignored_paths_are_skipped(tmp_path: Path) -> None:
    embedder = CountingEmbedder()
    indexer, store = _indexer(tmp_path, embedder)
    (indexer.root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (indexer.root / "ignored").mkdir()
    (indexer.root / "ignored" / "a.py").write_text("value = 1\n", encoding="utf-8")
    (indexer.root / "kept.py").write_text("value = 2\n", encoding="utf-8")

    indexer.scan()

    assert "ignored/a.py" not in store.known_files()
    assert "kept.py" in store.known_files()


def test_the_import_graph_is_recorded_with_resolved_targets(tmp_path: Path) -> None:
    embedder = CountingEmbedder()
    indexer, store = _indexer(tmp_path, embedder)
    (indexer.root / "store.py").write_text("value = 1\n", encoding="utf-8")
    (indexer.root / "app.py").write_text("from store import value\n", encoding="utf-8")

    indexer.scan()

    assert store.neighbours_of(["app.py"]) == {"store.py"}


def test_indexing_survives_an_embedder_outage_and_keeps_the_text_searchable(tmp_path: Path) -> None:
    class BrokenEmbedder(CountingEmbedder):
        def embed(self, texts):
            from agentos.retrieval.ports import EmbeddingUnavailable
            raise EmbeddingUnavailable("down")

    embedder = BrokenEmbedder()
    indexer, store = _indexer(tmp_path, embedder)
    (indexer.root / "a.py").write_text("def authorize():\n    return 1\n", encoding="utf-8")

    indexer.scan()

    assert store.status().chunks >= 1
    assert store.status().vectors == 0
    assert store.search_lexical("authorize", limit=5)
