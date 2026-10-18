from __future__ import annotations

from pathlib import Path

from agentos.permissions.trust import WorkspaceTrustStore


def test_trust_is_per_user_and_canonical(tmp_path: Path) -> None:
    store = WorkspaceTrustStore(tmp_path / "trust.json")
    root = tmp_path / "repo"
    root.mkdir()
    assert store.is_trusted("u1", root) is False
    store.trust("u1", root / "." )
    assert store.is_trusted("u1", root) is True
    assert store.is_trusted("u2", root) is False
    store.revoke("u1", root)
    assert store.is_trusted("u1", root) is False


def test_corrupt_store_trusts_nothing(tmp_path: Path) -> None:
    path = tmp_path / "trust.json"
    path.write_text("{oops", encoding="utf-8")
    store = WorkspaceTrustStore(path)
    assert store.is_trusted("u1", tmp_path) is False


def test_child_directory_is_not_trusted_by_parent_trust(tmp_path: Path) -> None:
    store = WorkspaceTrustStore(tmp_path / "trust.json")
    (tmp_path / "repo" / "sub").mkdir(parents=True)
    store.trust("u1", tmp_path / "repo")
    assert store.is_trusted("u1", tmp_path / "repo" / "sub") is False
