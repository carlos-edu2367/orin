"""Tests for purging unreadable, oversized, binary, and non-UTF-8 files (WS-D1 / D-RF2).

Covers:
- Indexed file overwritten with invalid UTF-8 bytes is forgotten on full scan and ``only=[...]``.
- Indexed file overwritten with binary NUL bytes is forgotten on full scan and ``only=[...]``.
- Indexed file that grows past ``MAX_FILE_BYTES`` (2 MB) is forgotten on full scan, ``only=[...]``,
  and direct ``_index_one``.
- Indexed file that raises ``OSError`` on read is forgotten on full scan and ``only=[...]``.
- Atomic swap in ``SqliteChunkStore.replace_file`` and lightweight ``file_state`` accessor.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

import pytest

from agentos.retrieval.chunking import HeuristicChunker
from agentos.retrieval.indexer import MAX_FILE_BYTES, ProjectIndexer
from agentos.retrieval.models import Chunk, EmbedderIdentity
from agentos.retrieval.store import SqliteChunkStore


class _CountingEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="fake", model="test", dim=4)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(text) % 7), 1.0, 0.0, 0.0] for text in texts]


def _make_indexer(tmp_path: Path) -> tuple[ProjectIndexer, SqliteChunkStore, Path]:
    embedder = _CountingEmbedder()
    root = tmp_path / "projeto ação"
    root.mkdir(parents=True, exist_ok=True)
    store = SqliteChunkStore(tmp_path / "index.db", embedder.identity)
    indexer = ProjectIndexer(root=root, store=store, chunker=HeuristicChunker(), embedder=embedder)
    return indexer, store, root


def _bump_mtime(path: Path, delta_ns: int = 5_000_000_000) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + delta_ns))


@pytest.mark.parametrize("use_only", [False, True])
def test_file_becoming_invalid_utf8_is_forgotten_on_next_scan(tmp_path: Path, use_only: bool) -> None:
    indexer, store, root = _make_indexer(tmp_path)
    rel = "pasta espaço/módulo ação.py"
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def authorize_secret_token():\n    return 1\n", encoding="utf-8")
    indexer.scan()

    assert rel in store.known_files()
    assert store.search_lexical("authorize_secret_token", limit=5)

    target.write_bytes(b"\xff\xfe\x80\x81invalid_utf8")
    _bump_mtime(target)

    if use_only:
        indexer.scan(only=[rel])
    else:
        indexer.scan()

    assert rel not in store.known_files()
    assert store.file_state(rel) is None
    assert store.status().files == 0
    assert store.status().chunks == 0
    assert store.search_lexical("authorize_secret_token", limit=5) == []


@pytest.mark.parametrize("use_only", [False, True])
def test_file_becoming_binary_with_nul_bytes_is_forgotten(tmp_path: Path, use_only: bool) -> None:
    indexer, store, root = _make_indexer(tmp_path)
    rel = "a.py"
    target = root / rel
    target.write_text("def parse_manifest():\n    return 'ok'\n", encoding="utf-8")
    indexer.scan()

    assert store.search_lexical("parse_manifest", limit=5)

    # Valid ASCII mixed with NUL bytes: decode('utf-8') alone would succeed, must still be rejected as binary.
    target.write_bytes(b"ELF\x00\x01\x02parse_manifest\x00binary")
    _bump_mtime(target)

    if use_only:
        indexer.scan(only=[rel])
    else:
        indexer.scan()

    assert rel not in store.known_files()
    assert store.status().chunks == 0
    assert store.search_lexical("parse_manifest", limit=5) == []


@pytest.mark.parametrize("mode", ["full", "only", "direct"])
def test_file_exceeding_2mb_leaves_the_index(tmp_path: Path, mode: str) -> None:
    indexer, store, root = _make_indexer(tmp_path)
    rel = "a.py"
    target = root / rel
    target.write_text("def giant_token_func():\n    return 42\n", encoding="utf-8")
    indexer.scan()

    assert rel in store.known_files()
    assert store.search_lexical("giant_token_func", limit=5)

    target.write_bytes(b"# " + b"x" * (MAX_FILE_BYTES + 128))
    _bump_mtime(target)

    if mode == "full":
        indexer.scan()
    elif mode == "only":
        indexer.scan(only=[rel])
    else:
        indexer._index_one(rel, store.known_files())

    assert rel not in store.known_files()
    assert store.file_state(rel) is None
    assert store.status().files == 0
    assert store.status().chunks == 0
    assert store.search_lexical("giant_token_func", limit=5) == []


@pytest.mark.parametrize("use_only", [False, True])
def test_file_raising_oserror_on_read_is_forgotten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_only: bool
) -> None:
    indexer, store, root = _make_indexer(tmp_path)
    rel = "locked.py"
    target = root / rel
    target.write_text("def locked_handler():\n    return 99\n", encoding="utf-8")
    indexer.scan()

    assert rel in store.known_files()

    # Change size/mtime so _index_one attempts to read the file, then simulate PermissionError on read_bytes.
    target.write_text("def locked_handler():\n    return 10000\n", encoding="utf-8")
    _bump_mtime(target)
    resolved_target = target.resolve()
    original_read_bytes = Path.read_bytes

    def _failing_read_bytes(self: Path) -> bytes:
        if self.resolve() == resolved_target:
            raise PermissionError("access denied")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _failing_read_bytes)

    if use_only:
        indexer.scan(only=[rel])
    else:
        indexer.scan()

    assert rel not in store.known_files()
    assert store.file_state(rel) is None
    assert store.status().files == 0
    assert store.status().chunks == 0
    assert store.search_lexical("locked_handler", limit=5) == []


def test_store_replace_file_swaps_chunks_atomically_and_updates_file_state(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", EmbedderIdentity(embedder_id="fake", model="test", dim=3))

    store.replace_file(
        "src/módulo ação.py",
        content_hash="h1",
        size_bytes=120,
        mtime_ns=1_000_000,
        language="python",
        chunks=[
            Chunk(
                path="src/módulo ação.py",
                start_line=1,
                end_line=3,
                symbol="old_fn",
                kind="definition",
                text="def old_fn():\n    return 1\n",
            )
        ],
    )
    assert store.file_state("src/módulo ação.py") == (1_000_000, 120)

    store.replace_file(
        "src/módulo ação.py",
        content_hash="h2",
        size_bytes=145,
        mtime_ns=2_000_000,
        language="python",
        chunks=[
            Chunk(
                path="src/módulo ação.py",
                start_line=1,
                end_line=4,
                symbol="new_fn",
                kind="definition",
                text="def new_fn():\n    return 2\n",
            )
        ],
    )

    assert store.file_state("src/módulo ação.py") == (2_000_000, 145)
    assert store.status().files == 1
    assert store.status().chunks == 1
    assert store.search_lexical("old_fn", limit=5) == []
    assert store.search_lexical("new_fn", limit=5)

    store.forget_file("src/módulo ação.py")
    assert store.file_state("src/módulo ação.py") is None
