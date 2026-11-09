"""A tool call must never wait on the index scan.

The first scan of a large project runs for minutes (walk, chunk, embed). When
``project_map``/``search_code`` ran that scan inline, the whole turn sat on
"Executando project_map" and every tool call queued behind it looked hung.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Sequence

from agentos.retrieval.chunking import HeuristicChunker
from agentos.retrieval.indexer import ProjectIndexer
from agentos.retrieval.models import EmbedderIdentity
from agentos.retrieval.service import RetrievalService
from agentos.retrieval.store import SqliteChunkStore
from agentos.retrieval.worker import IndexWorker


class _Embedder:
    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="fake", model="test", dim=4)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.5, 0.25, 0.1] for _ in texts]


class _SlowIndexer(ProjectIndexer):
    """A real indexer whose scan takes ``delay`` seconds and records overlap."""

    def __init__(self, *, delay: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self.delay = delay
        self.calls = 0
        self.running = 0
        self.max_running = 0
        self._guard = threading.Lock()

    def scan(self, only=None, *, embed: bool = True) -> None:
        with self._guard:
            self.calls += 1
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            time.sleep(self.delay)
            super().scan(only, embed=embed)
        finally:
            with self._guard:
                self.running -= 1


def _build(tmp_path: Path, *, delay: float) -> tuple[RetrievalService, _SlowIndexer, Path]:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "app.py").write_text("def hello():\n    return 42\n", encoding="utf-8")
    (root / "main.py").write_text("from app import hello\n\ndef run():\n    return hello()\n", encoding="utf-8")
    embedder = _Embedder()
    store = SqliteChunkStore(tmp_path / "index.db", embedder.identity)
    indexer = _SlowIndexer(delay=delay, root=root, store=store, chunker=HeuristicChunker(), embedder=embedder)
    return RetrievalService(store=store, indexer=indexer, embedder=embedder), indexer, root


def _timed(fn):
    started = time.monotonic()
    value = fn()
    return value, time.monotonic() - started


def test_with_a_worker_attached_tool_calls_answer_at_once_and_the_scan_runs_in_background(tmp_path: Path) -> None:
    service, indexer, _root = _build(tmp_path, delay=1.5)
    worker = IndexWorker(service)
    service.attach_background_refresh(worker.enqueue_full_scan)
    worker.start()
    try:
        assert service.indexing is True  # never scanned yet

        entries, elapsed = _timed(lambda: service.project_map(limit=5))
        assert elapsed < 0.5
        assert entries == []  # nothing indexed yet, but no hang

        result, elapsed = _timed(lambda: service.search("hello", limit=5))
        assert elapsed < 0.5
        assert result.hits == []

        assert worker.wait_idle(timeout=10)
        assert indexer.calls == 1  # the two tool calls queued a single refresh
        assert service.indexing is False
        entries = service.project_map(limit=5)
        assert any(entry["path"] == "app.py" for entry in entries)
    finally:
        worker.stop()


def test_a_tool_call_does_not_wait_while_the_worker_is_scanning(tmp_path: Path) -> None:
    service, indexer, _root = _build(tmp_path, delay=1.5)
    worker = IndexWorker(service)
    service.attach_background_refresh(worker.enqueue_full_scan)
    worker.start()
    try:
        worker.enqueue_full_scan()
        deadline = time.monotonic() + 2
        while indexer.running == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert indexer.running == 1
        assert service.indexing is True

        _entries, elapsed = _timed(lambda: service.project_map(limit=5))
        assert elapsed < 0.5
        _result, elapsed = _timed(lambda: service.search("hello", limit=5))
        assert elapsed < 0.5

        assert worker.wait_idle(timeout=10)
        assert indexer.calls == 1  # no second scan queued while one was running
    finally:
        worker.stop()


def test_without_a_worker_a_second_caller_skips_instead_of_scanning_concurrently(tmp_path: Path) -> None:
    service, indexer, _root = _build(tmp_path, delay=1.0)
    first = threading.Thread(target=lambda: service.project_map(limit=5))
    first.start()
    deadline = time.monotonic() + 2
    while indexer.running == 0 and time.monotonic() < deadline:
        time.sleep(0.01)

    _entries, elapsed = _timed(lambda: service.project_map(limit=5))
    first.join(timeout=10)

    assert elapsed < 0.5
    assert indexer.calls == 1
    assert indexer.max_running == 1


def test_worker_reindex_and_inline_refresh_never_overlap(tmp_path: Path) -> None:
    service, indexer, root = _build(tmp_path, delay=0.3)
    threads = [threading.Thread(target=service.reindex) for _ in range(3)]
    threads += [threading.Thread(target=lambda: service.reindex(["app.py"])) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert indexer.max_running == 1
    assert indexer.calls == 5
