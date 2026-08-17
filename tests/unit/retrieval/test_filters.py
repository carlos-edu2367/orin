from __future__ import annotations

from pathlib import Path

from agentos.retrieval.filters import GitignoreFilter, IndexFilter


def test_the_fixed_denylist_rejects_build_and_vendor_directories() -> None:
    index_filter = IndexFilter(GitignoreFilter(()))

    assert index_filter.rejects("node_modules/react/index.js")
    assert index_filter.rejects(".git/config")
    assert index_filter.rejects("src/__pycache__/a.pyc")
    assert index_filter.rejects("uv.lock")
    assert not index_filter.rejects("src/agentos/retrieval/store.py")


def test_secrets_are_rejected_before_anything_reads_them() -> None:
    index_filter = IndexFilter(GitignoreFilter(()))

    assert index_filter.rejects(".env")
    assert index_filter.rejects(".env.local")
    assert index_filter.rejects("deploy/server.pem")
    assert index_filter.rejects("keys/id_rsa")
    assert not index_filter.rejects(".env.example")


def test_gitignore_patterns_are_honoured() -> None:
    ignore = GitignoreFilter.parse("# comment\n\ndist/\n*.log\n/root-only.txt\n!keep.log\n")

    assert ignore.ignores("dist/app.js")
    assert ignore.ignores("logs/run.log")
    assert ignore.ignores("root-only.txt")
    assert not ignore.ignores("nested/root-only.txt")
    assert not ignore.ignores("keep.log")
    assert not ignore.ignores("src/a.py")


def test_gitignore_is_read_from_the_project_root(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")

    ignore = GitignoreFilter.from_root(tmp_path)

    assert ignore.ignores("build/out.js")
    assert not ignore.ignores("src/a.py")


def test_a_missing_gitignore_rejects_nothing(tmp_path: Path) -> None:
    assert GitignoreFilter.from_root(tmp_path).ignores("anything.py") is False
