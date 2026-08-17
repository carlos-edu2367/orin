from __future__ import annotations

from pathlib import Path

from agentos.retrieval.models import EmbedderIdentity
from agentos.retrieval.store import SqliteChunkStore


def test_neighbours_span_both_directions_of_the_graph(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", EmbedderIdentity("fake", "test", 3))
    store.replace_imports("src/app.py", ("src/store.py",))
    store.replace_imports("src/cli.py", ("src/app.py",))

    assert store.neighbours_of(["src/app.py"]) == {"src/store.py", "src/cli.py"}


def test_replacing_imports_removes_the_previous_edges(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", EmbedderIdentity("fake", "test", 3))
    store.replace_imports("src/app.py", ("src/store.py",))

    store.replace_imports("src/app.py", ("src/other.py",))

    assert store.neighbours_of(["src/app.py"]) == {"src/other.py"}


def test_the_most_connected_files_come_back_ordered(tmp_path: Path) -> None:
    store = SqliteChunkStore(tmp_path / "index.db", EmbedderIdentity("fake", "test", 3))
    store.replace_imports("src/a.py", ("src/core.py",))
    store.replace_imports("src/b.py", ("src/core.py",))
    store.replace_imports("src/c.py", ("src/side.py",))

    assert store.most_imported(limit=2) == [("src/core.py", 2), ("src/side.py", 1)]
