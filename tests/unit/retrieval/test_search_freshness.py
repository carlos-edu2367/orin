"""Tests for query-time freshness verification (WS-D1 / D-RF1).

Covers:
- External edit before the 60 s full-scan interval returns new content or no hit,
  never stale content (including paths with spaces and accents).
- Deleted file is forgotten from the index and removed from search results.
- Exceeding the 200 ms budget discards stale results and schedules async reindex
  (using an injectable fake clock, without any fixed sleep).
- Intact files are never reindexed (observed via ``store.replace_file`` count).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from agentos.retrieval.chunking import HeuristicChunker
from agentos.retrieval.embeddings.lexical import LexicalOnlyEmbedder
from agentos.retrieval.freshness import FreshnessChecker
from agentos.retrieval.indexer import ProjectIndexer
from agentos.retrieval.models import EmbedderIdentity, SearchHit
from agentos.retrieval.service import RetrievalService
from agentos.retrieval.store import SqliteChunkStore
from agentos.retrieval.worker import IndexWorker


class _KeywordEmbedder:
    KEYWORDS = ("authorize", "billing", "policy", "token")

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="fake", model="test", dim=len(self.KEYWORDS))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(text.lower().count(word)) + 0.01 for word in self.KEYWORDS] for text in texts]


class _ListClock:
    """Deterministic monotonic clock backed by an explicit timeline (seconds)."""

    def __init__(self, times: Sequence[float]) -> None:
        self._times = list(times)
        self._idx = 0

    def __call__(self) -> float:
        value = self._times[min(self._idx, len(self._times) - 1)]
        self._idx += 1
        return value


def _build_service(
    tmp_path: Path,
    *,
    embedder=None,
    clock=None,
    freshness_budget_ms: float = 200.0,
    folder_name: str = "projeto código ação",
) -> tuple[RetrievalService, ProjectIndexer, SqliteChunkStore, Path]:
    embedder = embedder or LexicalOnlyEmbedder()
    root = tmp_path / folder_name
    root.mkdir(parents=True, exist_ok=True)
    store = SqliteChunkStore(tmp_path / "index.db", embedder.identity)
    indexer = ProjectIndexer(root=root, store=store, chunker=HeuristicChunker(), embedder=embedder)
    kwargs: dict[str, object] = {"freshness_budget_ms": freshness_budget_ms}
    if clock is not None:
        kwargs["clock"] = clock
    service = RetrievalService(store=store, indexer=indexer, embedder=embedder, **kwargs)
    return service, indexer, store, root


def _bump_mtime(path: Path, delta_ns: int = 5_000_000_000) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + delta_ns))


def test_external_edit_before_60s_returns_updated_content_never_stale(tmp_path: Path) -> None:
    service, indexer, _store, root = _build_service(tmp_path, embedder=_KeywordEmbedder())
    target = root / "a.py"
    target.write_text("def authorize_billing():\n    return 'LEGACY_RATE_10'\n", encoding="utf-8")
    indexer.scan()

    # Edit outside Orin immediately (< 60 s after scan).
    target.write_text(
        "def authorize_billing():\n    return 'UPDATED_RATE_25_EXACT'\n",
        encoding="utf-8",
    )
    _bump_mtime(target)

    result = service.search("authorize_billing", limit=5)

    assert result.hits
    assert all("LEGACY_RATE_10" not in hit.text for hit in result.hits)
    assert any("UPDATED_RATE_25_EXACT" in hit.text for hit in result.hits)


def test_external_edit_removing_term_returns_no_stale_hit(tmp_path: Path) -> None:
    service, indexer, _store, root = _build_service(tmp_path, embedder=LexicalOnlyEmbedder())
    target = root / "pasta módulo" / "cálculo ação.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def legacy_symbol_alpha():\n    return 'OLD_CONTENT_ALPHA'\n", encoding="utf-8")
    indexer.scan()

    # Edit outside Orin removing the searched symbol altogether.
    target.write_text("def completely_different():\n    return 'BRAND_NEW_VALUE'\n", encoding="utf-8")
    _bump_mtime(target)

    result = service.search("legacy_symbol_alpha", limit=5)

    assert all("OLD_CONTENT_ALPHA" not in hit.text for hit in result.hits)
    assert result.hits == []


def test_external_edit_in_path_with_spaces_and_accents_returns_fresh_content(tmp_path: Path) -> None:
    service, indexer, _store, root = _build_service(tmp_path, embedder=LexicalOnlyEmbedder())
    target = root / "módulos ação" / "serviço cobrança.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def cobrar_fatura():\n    return 'VERSAO_ANTIGA'\n", encoding="utf-8")
    indexer.scan()

    target.write_text("def cobrar_fatura():\n    return 'VERSAO_ATUALIZADA_FINAL'\n", encoding="utf-8")
    _bump_mtime(target)

    result = service.search("cobrar_fatura", limit=5)

    assert len(result.hits) == 1
    assert result.hits[0].path == "módulos ação/serviço cobrança.py"
    assert "VERSAO_ATUALIZADA_FINAL" in result.hits[0].text
    assert "VERSAO_ANTIGA" not in result.hits[0].text


def test_deleted_file_is_dropped_from_results_and_forgotten_from_index(tmp_path: Path) -> None:
    service, indexer, store, root = _build_service(tmp_path, embedder=LexicalOnlyEmbedder())
    doomed = root / "obsoleto ação.py"
    survivor = root / "ativo.py"
    doomed.write_text("def verify_token():\n    return 'FROM_DELETED_FILE'\n", encoding="utf-8")
    survivor.write_text("def verify_token():\n    return 'FROM_SURVIVOR_FILE'\n", encoding="utf-8")
    indexer.scan()

    doomed.unlink()

    result = service.search("verify_token", limit=2)

    assert len(result.hits) == 1
    assert result.hits[0].path == "ativo.py"
    assert "FROM_SURVIVOR_FILE" in result.hits[0].text
    assert store.file_state("obsoleto ação.py") is None
    assert "obsoleto ação.py" not in store.known_files()
    assert store.status().files == 1


def test_budget_exceeded_discards_stale_result_and_schedules_async_reindex(tmp_path: Path) -> None:
    # Clock starts at 10.0 s; by the time the stale file check runs, 250 ms elapsed (> 200 ms budget).
    fake_clock = _ListClock([10.0, 10.25, 10.30])
    service, indexer, store, root = _build_service(
        tmp_path,
        embedder=LexicalOnlyEmbedder(),
        clock=fake_clock,
        freshness_budget_ms=200.0,
    )
    stale = root / "stale.py"
    fresh = root / "fresh.py"
    stale.write_text("def check_policy():\n    return 'STALE_TEXT_MUST_NOT_APPEAR'\n", encoding="utf-8")
    fresh.write_text("def check_policy():\n    return 'FRESH_TEXT_OK'\n", encoding="utf-8")
    indexer.scan()

    stale.write_text("def check_policy():\n    return 'NEW_TEXT_DEFERRED'\n", encoding="utf-8")
    _bump_mtime(stale)

    scheduled_paths: list[list[str]] = []
    service.attach_background_reindex(lambda paths: scheduled_paths.append(list(paths)))

    replace_calls = 0
    original_replace = store.replace_file

    def _counting_replace(*args, **kwargs):
        nonlocal replace_calls
        replace_calls += 1
        return original_replace(*args, **kwargs)

    store.replace_file = _counting_replace  # type: ignore[method-assign]

    result = service.search("check_policy", limit=5)

    assert replace_calls == 0
    assert all(hit.path != "stale.py" for hit in result.hits)
    assert all("STALE_TEXT_MUST_NOT_APPEAR" not in hit.text for hit in result.hits)
    assert [hit.path for hit in result.hits] == ["fresh.py"]
    assert scheduled_paths == [["stale.py"]]


def test_budget_exceeded_falls_back_to_background_refresh_callback(tmp_path: Path) -> None:
    fake_clock = _ListClock([1.0, 1.3])
    service, indexer, _store, root = _build_service(
        tmp_path,
        embedder=LexicalOnlyEmbedder(),
        clock=fake_clock,
        freshness_budget_ms=200.0,
    )
    target = root / "a.py"
    target.write_text("def check_policy():\n    return 'OLD_BODY'\n", encoding="utf-8")
    indexer.scan()

    target.write_text("def check_policy():\n    return 'UPDATED_BODY_LONGER'\n", encoding="utf-8")
    _bump_mtime(target)

    refresh_calls = 0

    def _on_refresh() -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    service.attach_background_refresh(_on_refresh)

    result = service.search("check_policy", limit=5)

    assert result.hits == []
    assert refresh_calls == 1


def test_intact_file_is_not_reindexed_during_search(tmp_path: Path) -> None:
    service, indexer, store, root = _build_service(tmp_path, embedder=LexicalOnlyEmbedder())
    target = root / "intact.py"
    target.write_text("def authorize_user():\n    return True\n", encoding="utf-8")
    indexer.scan()

    replace_calls = 0
    original_replace = store.replace_file

    def _counting_replace(*args, **kwargs):
        nonlocal replace_calls
        replace_calls += 1
        return original_replace(*args, **kwargs)

    store.replace_file = _counting_replace  # type: ignore[method-assign]

    result = service.search("authorize_user", limit=5)

    assert len(result.hits) == 1
    assert result.hits[0].path == "intact.py"
    assert replace_calls == 0


def test_freshness_checker_filter_handles_partial_budget_across_multiple_stale_files(tmp_path: Path) -> None:
    root = tmp_path / "workspace espaço"
    root.mkdir()
    embedder = LexicalOnlyEmbedder()
    store = SqliteChunkStore(tmp_path / "index.db", embedder.identity)
    indexer = ProjectIndexer(root=root, store=store, chunker=HeuristicChunker(), embedder=embedder)

    first = root / "first.py"
    second = root / "second.py"
    first.write_text("def alpha():\n    return 'OLD_FIRST'\n", encoding="utf-8")
    second.write_text("def alpha():\n    return 'OLD_SECOND'\n", encoding="utf-8")
    indexer.scan()

    first.write_text("def alpha():\n    return 'NEW_FIRST_CONTENT'\n", encoding="utf-8")
    second.write_text("def alpha():\n    return 'NEW_SECOND_CONTENT'\n", encoding="utf-8")
    _bump_mtime(first)
    _bump_mtime(second)

    # Start at 0.0 s; first check at 0.05 s (50 ms -> inline reindex); second check at 0.25 s (250 ms -> deferred).
    clock = _ListClock([0.0, 0.05, 0.25])
    deferred: list[list[str]] = []
    checker = FreshnessChecker(
        store=store,
        indexer=indexer,
        workspace_root=root,
        schedule_reindex=lambda paths: deferred.append(list(paths)),
        clock=clock,
    )
    stale_hits = [
        SearchHit(path="first.py", start_line=1, end_line=2, symbol="alpha", score=0.5, text="OLD_FIRST"),
        SearchHit(path="second.py", start_line=1, end_line=2, symbol="alpha", score=0.4, text="OLD_SECOND"),
    ]

    filtered = checker.filter(stale_hits, limit=2, budget_ms=200.0)

    assert len(filtered) == 1
    assert filtered[0].path == "first.py"
    assert "NEW_FIRST_CONTENT" in filtered[0].text
    assert "OLD_FIRST" not in filtered[0].text
    assert deferred == [["second.py"]]


def test_edited_file_that_becomes_invalid_utf8_is_purged_during_search(tmp_path: Path) -> None:
    service, indexer, store, root = _build_service(tmp_path, embedder=LexicalOnlyEmbedder())
    target = root / "corrupt.py"
    target.write_text("def authorize_payment():\n    return 'VALID_TEXT'\n", encoding="utf-8")
    indexer.scan()

    target.write_bytes(b"\xff\xfe\x80\x81\x90\x91")
    _bump_mtime(target)

    result = service.search("authorize_payment", limit=5)

    assert result.hits == []
    assert store.file_state("corrupt.py") is None
    assert "corrupt.py" not in store.known_files()


def test_touched_file_with_identical_content_syncs_stat_without_replacing_chunks(tmp_path: Path) -> None:
    service, indexer, store, root = _build_service(tmp_path, embedder=LexicalOnlyEmbedder())
    target = root / "touched.py"
    target.write_text("def authorize_touch():\n    return 'SAME_BODY'\n", encoding="utf-8")
    indexer.scan()

    _bump_mtime(target)

    replace_calls = 0
    original_replace = store.replace_file

    def _counting_replace(*args, **kwargs):
        nonlocal replace_calls
        replace_calls += 1
        return original_replace(*args, **kwargs)

    store.replace_file = _counting_replace  # type: ignore[method-assign]

    first = service.search("authorize_touch", limit=5)
    assert len(first.hits) == 1
    assert "SAME_BODY" in first.hits[0].text
    assert replace_calls == 0

    st = target.stat()
    assert store.file_state("touched.py") == (st.st_mtime_ns, st.st_size)


def test_budget_exceeded_queues_targeted_reindex_on_attached_index_worker(tmp_path: Path) -> None:
    # First search exceeds budget (0.0 -> 0.25 s); second search is after worker reindexes.
    fake_clock = _ListClock([0.0, 0.25, 1.0, 1.01])
    service, indexer, _store, root = _build_service(
        tmp_path,
        embedder=LexicalOnlyEmbedder(),
        clock=fake_clock,
        freshness_budget_ms=200.0,
    )
    target = root / "worker_stale.py"
    target.write_text("def authorize_worker():\n    return 'OLD_WORKER_VAL'\n", encoding="utf-8")
    indexer.scan()

    worker = IndexWorker(service)
    service.attach_background_refresh(worker.enqueue_full_scan)
    worker.start()
    try:
        target.write_text("def authorize_worker():\n    return 'NEW_WORKER_VAL_ASYNC'\n", encoding="utf-8")
        _bump_mtime(target)

        first = service.search("authorize_worker", limit=5)
        assert first.hits == []

        assert worker.wait_idle(timeout=5.0)

        second = service.search("authorize_worker", limit=5)
        assert len(second.hits) == 1
        assert "NEW_WORKER_VAL_ASYNC" in second.hits[0].text
        assert "OLD_WORKER_VAL" not in second.hits[0].text
    finally:
        worker.stop()


def test_busy_scan_lock_never_blocks_search_and_discards_stale_hit(tmp_path: Path) -> None:
    service, indexer, _store, root = _build_service(tmp_path, embedder=LexicalOnlyEmbedder())
    target = root / "busy.py"
    target.write_text("def authorize_busy():\n    return 'OLD_BUSY'\n", encoding="utf-8")
    indexer.scan()

    target.write_text("def authorize_busy():\n    return 'NEW_BUSY_TEXT'\n", encoding="utf-8")
    _bump_mtime(target)

    scheduled: list[list[str]] = []
    service.attach_background_reindex(lambda paths: scheduled.append(list(paths)))

    # Simulate an active background scan holding _scan_lock.
    assert service._scan_lock.acquire(blocking=False)
    try:
        result = service.search("authorize_busy", limit=5)
    finally:
        service._scan_lock.release()

    assert result.hits == []
    assert scheduled == [["busy.py"]]

