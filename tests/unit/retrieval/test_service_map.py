from __future__ import annotations

from pathlib import Path

from tests.unit.retrieval.test_service import KeywordEmbedder
from agentos.retrieval.chunking import HeuristicChunker
from agentos.retrieval.indexer import ProjectIndexer
from agentos.retrieval.service import RetrievalService
from agentos.retrieval.store import SqliteChunkStore


def _service(tmp_path: Path) -> tuple[RetrievalService, ProjectIndexer]:
    embedder = KeywordEmbedder()
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    store = SqliteChunkStore(tmp_path / "index.db", embedder.identity)
    indexer = ProjectIndexer(root=root, store=store, chunker=HeuristicChunker(), embedder=embedder)
    return RetrievalService(store=store, indexer=indexer, embedder=embedder), indexer


def test_the_map_ranks_the_most_imported_files_and_lists_their_symbols(tmp_path: Path) -> None:
    service, indexer = _service(tmp_path)
    (indexer.root / "core.py").write_text("def alpha():\n    return 1\n\ndef beta():\n    return 2\n", encoding="utf-8")
    (indexer.root / "one.py").write_text("from core import alpha\n", encoding="utf-8")
    (indexer.root / "two.py").write_text("from core import beta\n", encoding="utf-8")
    indexer.scan()

    entries = service.project_map(limit=5)

    assert entries[0]["path"] == "core.py"
    assert entries[0]["imported_by"] == 2
    assert entries[0]["symbols"] == ["alpha", "beta"]


def test_the_map_of_an_empty_project_is_empty(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    assert service.project_map(limit=5) == []
