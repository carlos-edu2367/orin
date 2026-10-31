from __future__ import annotations

from pathlib import Path
from typing import Sequence

from agentos.retrieval.chunking import HeuristicChunker
from agentos.retrieval.indexer import EMBED_BATCH, ProjectIndexer
from agentos.retrieval.models import EmbedderIdentity
from agentos.retrieval.service import RetrievalService
from agentos.retrieval.store import SqliteChunkStore


class _FailingEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="ollama", model="nomic-embed-text", dim=4)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        raise RuntimeError("Ollama /api/embed 404 Not Found")


class _EmptyEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="ollama", model="nomic-embed-text", dim=4)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls >= 5:
            raise RuntimeError("infinite loop detected on empty embed result")
        return []


class _PartialEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="ollama", model="nomic-embed-text", dim=4)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls >= 5:
            raise RuntimeError("infinite loop detected on partial embed result")
        return [[1.0, 0.0, 0.0, 0.0]]


class _HealthyEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="fake", model="test", dim=4)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [[1.0, 0.5, 0.25, 0.1] for _ in texts]


def _build_service(tmp_path: Path, embedder) -> tuple[RetrievalService, ProjectIndexer, SqliteChunkStore, Path]:
    root = tmp_path / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    store = SqliteChunkStore(tmp_path / ".db" / "index.db", embedder.identity)
    indexer = ProjectIndexer(root=root, store=store, chunker=HeuristicChunker(), embedder=embedder)
    service = RetrievalService(store=store, indexer=indexer, embedder=embedder)
    return service, indexer, store, root


def test_project_map_does_not_call_embedder(tmp_path: Path) -> None:
    embedder = _FailingEmbedder()
    service, _indexer, store, root = _build_service(tmp_path, embedder)
    (root / "app.py").write_text("def hello():\n    return 42\n", encoding="utf-8")
    (root / "main.py").write_text("from app import hello\n\ndef run():\n    return hello()\n", encoding="utf-8")

    entries = service.project_map(limit=10)

    assert any(entry["path"] == "app.py" and "hello" in entry["symbols"] for entry in entries)
    assert embedder.calls == 0
    assert store.status().files == 2
    assert store.status().vectors == 0


def test_scan_embed_false_skips_embedding_completely(tmp_path: Path) -> None:
    embedder = _FailingEmbedder()
    _service, indexer, store, root = _build_service(tmp_path, embedder)
    (root / "service.py").write_text("def compute():\n    return 1\n", encoding="utf-8")

    indexer.scan(embed=False)

    assert embedder.calls == 0
    assert store.status().files == 1
    assert store.status().chunks >= 1
    assert store.status().vectors == 0


def test_scan_circuit_breaker_stops_on_first_failed_batch(tmp_path: Path) -> None:
    embedder = _FailingEmbedder()
    _service, indexer, store, root = _build_service(tmp_path, embedder)
    for idx in range(EMBED_BATCH + 10):
        (root / f"mod_{idx}.py").write_text(f"def fn_{idx}():\n    return {idx}\n", encoding="utf-8")

    indexer.scan(embed=True)

    assert store.status().chunks >= EMBED_BATCH + 10
    assert embedder.calls == 1
    assert store.status().vectors == 0


def test_scan_circuit_breaker_stops_on_empty_or_partial_embedding_result(tmp_path: Path) -> None:
    empty_embedder = _EmptyEmbedder()
    _service, indexer, store, root = _build_service(tmp_path / "empty", empty_embedder)
    for idx in range(10):
        (root / f"mod_{idx}.py").write_text(f"def fn_{idx}():\n    return {idx}\n", encoding="utf-8")

    indexer.scan(embed=True)
    assert empty_embedder.calls == 1
    assert store.status().vectors == 0

    partial_embedder = _PartialEmbedder()
    _service2, indexer2, store2, root2 = _build_service(tmp_path / "partial", partial_embedder)
    for idx in range(10):
        (root2 / f"mod_{idx}.py").write_text(f"def fn_{idx}():\n    return {idx}\n", encoding="utf-8")

    indexer2.scan(embed=True)
    assert partial_embedder.calls == 1
    assert store2.status().vectors == 1


def test_search_after_project_map_still_embeds_pending_chunks(tmp_path: Path) -> None:
    embedder = _HealthyEmbedder()
    service, _indexer, store, root = _build_service(tmp_path, embedder)
    (root / "app.py").write_text("def authorize_request():\n    return True\n", encoding="utf-8")
    (root / "caller.py").write_text("from app import authorize_request\n", encoding="utf-8")

    entries = service.project_map(limit=5)
    assert entries[0]["path"] == "app.py"
    assert embedder.calls == 0
    assert store.status().vectors == 0

    result = service.search("authorize_request", limit=5)
    assert result.mode == "semantic"
    assert result.hits
    assert store.status().vectors >= 1
    assert embedder.calls >= 2  # 1 batch embed + 1 query embed
