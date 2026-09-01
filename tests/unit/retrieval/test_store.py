from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agentos.retrieval.models import Chunk, EmbedderIdentity
from agentos.retrieval.store import SqliteChunkStore


def _identity() -> EmbedderIdentity:
    return EmbedderIdentity(embedder_id="fake", model="test", dim=3)


def _chunk(path: str, start: int, text: str, symbol: str | None = None) -> Chunk:
    return Chunk(path=path, start_line=start, end_line=start + 1, symbol=symbol, kind="definition" if symbol else "block", text=text)


def test_replacing_a_file_swaps_its_chunks(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", _identity())

    store.replace_file("src/a.py", content_hash="h1", size_bytes=10, mtime_ns=1, language="python", chunks=[_chunk("src/a.py", 1, "alpha")])
    store.replace_file("src/a.py", content_hash="h2", size_bytes=11, mtime_ns=2, language="python", chunks=[_chunk("src/a.py", 5, "beta")])

    assert store.status().chunks == 1
    assert store.known_files()["src/a.py"] == ("h2", 2, 11)


def test_forgetting_a_file_removes_its_chunks(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", _identity())
    store.replace_file("src/a.py", content_hash="h1", size_bytes=10, mtime_ns=1, language="python", chunks=[_chunk("src/a.py", 1, "alpha")])

    store.forget_file("src/a.py")

    assert store.status().files == 0
    assert store.status().chunks == 0
    assert store.search_lexical("alpha", limit=10) == []


def test_lexical_search_ranks_by_bm25_and_matches_symbols(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", _identity())
    store.replace_file("src/a.py", content_hash="h", size_bytes=1, mtime_ns=1, language="python", chunks=[
        _chunk("src/a.py", 1, "authorize the tool policy", symbol="authorize"),
        _chunk("src/a.py", 10, "unrelated content about colours"),
    ])

    results = store.search_lexical("authorize policy", limit=10)

    assert results
    assert results[0][0] == "src/a.py:1-2"


def test_a_query_with_only_punctuation_returns_nothing(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", _identity())
    store.replace_file("src/a.py", content_hash="h", size_bytes=1, mtime_ns=1, language="python", chunks=[_chunk("src/a.py", 1, "alpha")])

    assert store.search_lexical("?? -- ((", limit=10) == []


def test_chunks_are_readable_by_id(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", _identity())
    store.replace_file("src/a.py", content_hash="h", size_bytes=1, mtime_ns=1, language="python", chunks=[_chunk("src/a.py", 1, "alpha", symbol="run")])

    loaded = store.chunks_by_id(["src/a.py:1-2", "missing:1-2"])

    assert list(loaded) == ["src/a.py:1-2"]
    assert loaded["src/a.py:1-2"].symbol == "run"


def test_the_last_scan_timestamp_round_trips(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", _identity())
    moment = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)

    store.mark_scanned(moment)

    assert store.status().last_scan_at == moment
