"""TDD tests for WS-B1: AgentWorktreeManager (B-RF1)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.code_mode.agent_worktree import (
    AgentWorktree,
    AgentWorktreeManager,
    IsolationUnavailableError,
    agent_worktree_slug,
)
from agentos.code_mode.worktree import WorktreeError
from agentos.persistence.postgres.agent_worktrees import AgentWorktreeStore
from agentos.persistence.postgres.schema import metadata


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "orin-test@example.com")
    _git(repo, "config", "user.name", "Orin Test")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / ".gitignore").write_text("node_modules/\n.venv/\n.env.local\n.orin/\n", encoding="utf-8")
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "app.py")
    _git(repo, "commit", "-m", "initial commit")
    return repo


def _make_store() -> AgentWorktreeStore:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    return AgentWorktreeStore(engine)


def test_agent_worktree_slug_handles_short_and_long_ids_with_colons() -> None:
    assert agent_worktree_slug("a1") == "a1"
    long_id = "agent:chat_0123456789abcdef0123456789abcdef:0123456789abcdef01234567"
    slug = agent_worktree_slug(long_id)
    assert ":" not in slug
    assert len(slug) <= 32
    assert slug == agent_worktree_slug(long_id)  # deterministic


def test_create_for_agent_refuses_non_git_directory(tmp_path: Path) -> None:
    folder = tmp_path / "plain-dir"
    folder.mkdir()
    mgr = AgentWorktreeManager(folder)
    with pytest.raises(IsolationUnavailableError) as exc_info:
        mgr.create_for_agent("a1")
    assert exc_info.value.reason == "not_git_repo"


def test_create_for_agent_refuses_submodules(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "with-submodules")
    (repo / ".gitmodules").write_text('[submodule "lib"]\n\tpath = lib\n\turl = https://example.com/lib.git\n', encoding="utf-8")
    mgr = AgentWorktreeManager(repo)
    with pytest.raises(IsolationUnavailableError) as exc_info:
        mgr.create_for_agent("a-sub")
    assert exc_info.value.reason == "submodules"


def test_create_for_agent_refuses_git_lfs(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "with-lfs")
    (repo / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8")
    mgr = AgentWorktreeManager(repo)
    with pytest.raises(IsolationUnavailableError) as exc_info:
        mgr.create_for_agent("a-lfs")
    assert exc_info.value.reason == "git_lfs"


def test_create_for_agent_spaces_and_accents_and_isolation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "área de trabalho revisão")
    store = _make_store()
    mgr = AgentWorktreeManager(repo, store=store)

    wt = mgr.create_for_agent("a1")
    assert isinstance(wt, AgentWorktree)
    assert wt.path.is_dir()
    assert wt.branch == "orin/agent-a1"
    assert wt.base_commit == _git(repo, "rev-parse", "HEAD").stdout.strip()

    # Child edits file inside worktree; parent stays untouched.
    (wt.path / "app.py").write_text("VALUE = 999\n", encoding="utf-8")
    assert (repo / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert mgr.has_agent_changes("a1") is True

    rec = store.get("a1")
    assert rec is not None
    assert rec.branch == "orin/agent-a1"
    assert rec.merge_state == "pending"

    assert mgr.discard_worktree("a1") is True
    assert not wt.path.exists()
    assert store.get("a1").merge_state == "discarded"


def test_base_uses_git_stash_create_for_dirty_tracked_without_mutating_parent_or_stash_list(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "dirty-parent")
    (repo / "app.py").write_text("VALUE = 42\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("hello untracked\n", encoding="utf-8")
    (repo / ".worktreeinclude").write_text(".env.local\n", encoding="utf-8")
    (repo / ".env.local").write_text("SECRET_TOKEN=demo\n", encoding="utf-8")

    before_status = _git(repo, "status", "--porcelain").stdout
    before_stash = _git(repo, "stash", "list").stdout

    mgr = AgentWorktreeManager(repo)
    wt = mgr.create_for_agent("dirty1")

    # Parent working tree and stash list must be untouched.
    assert _git(repo, "status", "--porcelain").stdout == before_status
    assert _git(repo, "stash", "list").stdout == before_stash
    assert wt.base_commit != _git(repo, "rev-parse", "HEAD").stdout.strip()

    # Worktree sees dirty tracked edits, untracked files, and .worktreeinclude files.
    assert (wt.path / "app.py").read_text(encoding="utf-8") == "VALUE = 42\n"
    assert (wt.path / "untracked.txt").read_text(encoding="utf-8") == "hello untracked\n"
    assert (wt.path / ".env.local").read_text(encoding="utf-8") == "SECRET_TOKEN=demo\n"

    # Before child makes its own edits, has_agent_changes is False!
    assert mgr.has_agent_changes("dirty1") is False

    # Criterion 5: child with no changes removes worktree and branch.
    store = _make_store()
    store.upsert(agent_id="dirty1", workspace_root=str(repo), base_commit=wt.base_commit, branch=wt.branch)
    assert mgr.cleanup_if_unchanged("dirty1", store=store) is True
    assert not wt.path.exists()
    branches = _git(repo, "branch", "--list", wt.branch).stdout.strip()
    assert branches == ""
    assert store.get("dirty1").merge_state == "empty"


def test_long_production_agent_id_with_colons_works_on_windows(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "prod-id")
    store = _make_store()
    agent_id = "agent:chat_0123456789abcdef0123456789abcdef:fedcba9876543210fedcba98"
    mgr = AgentWorktreeManager(repo, store=store)

    wt = mgr.create_for_agent(agent_id)
    assert wt.path.is_dir()
    assert ":" not in wt.branch
    assert wt.branch.startswith("orin/agent-")
    assert mgr.cleanup_if_unchanged(agent_id) is True
    assert not wt.path.exists()
    assert _git(repo, "branch", "--list", wt.branch).stdout.strip() == ""


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions are Windows-only")
def test_link_dirs_create_windows_junctions_and_discard_preserves_parent(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "win-junctions")
    nm_file = repo / "node_modules" / "pkg" / "index.js"
    nm_file.parent.mkdir(parents=True)
    nm_file.write_text("module.exports = 1;\n", encoding="utf-8")

    venv_file = repo / ".venv" / "Scripts" / "marker.txt"
    venv_file.parent.mkdir(parents=True)
    venv_file.write_text("venv-ok\n", encoding="utf-8")

    store = _make_store()
    mgr = AgentWorktreeManager(repo, store=store)
    wt = mgr.create_for_agent("win1", link_dirs=["node_modules", ".venv", "../escape", "C:/Windows"])

    assert os.path.isjunction(wt.path / "node_modules")
    assert os.path.isjunction(wt.path / ".venv")
    assert (wt.path / "node_modules" / "pkg" / "index.js").read_text(encoding="utf-8") == "module.exports = 1;\n"
    assert (wt.path / ".venv" / "Scripts" / "marker.txt").read_text(encoding="utf-8") == "venv-ok\n"
    assert set(wt.linked_dirs) == {"node_modules", ".venv"}

    # Child modifies a tracked file and creates a new file.
    (wt.path / "app.py").write_text("VALUE = 77\n", encoding="utf-8")
    (wt.path / "new_file.py").write_text("X = 1\n", encoding="utf-8")
    assert mgr.has_agent_changes("win1") is True

    # discard_worktree removes junctions safely, deletes worktree & branch, and preserves parent targets!
    assert mgr.discard_worktree("win1") is True
    assert not wt.path.exists()
    assert _git(repo, "branch", "--list", wt.branch).stdout.strip() == ""
    assert nm_file.is_file() and nm_file.read_text(encoding="utf-8") == "module.exports = 1;\n"
    assert venv_file.is_file() and venv_file.read_text(encoding="utf-8") == "venv-ok\n"
    assert (repo / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert store.get("win1").merge_state == "discarded"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink test runs outside Windows")
def test_link_dirs_create_posix_symlinks_and_discard_preserves_parent(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "posix-links")
    nm_file = repo / "node_modules" / "pkg" / "index.js"
    nm_file.parent.mkdir(parents=True)
    nm_file.write_text("module.exports = 1;\n", encoding="utf-8")

    mgr = AgentWorktreeManager(repo)
    wt = mgr.create_for_agent("posix1", link_dirs=["node_modules", "../escape"])
    assert (wt.path / "node_modules").is_symlink()
    assert mgr.discard_worktree("posix1") is True
    assert not wt.path.exists()
    assert nm_file.is_file() and nm_file.read_text(encoding="utf-8") == "module.exports = 1;\n"


def test_discard_worktree_reports_clear_error_when_file_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path / "locked-wt")
    mgr = AgentWorktreeManager(repo)
    wt = mgr.create_for_agent("locked1")

    def fake_run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        if "worktree" in args and "remove" in args:
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=255,
                stdout="",
                stderr="error: failed to delete worktree: Invalid argument",
            )
        return AgentWorktreeManager._run_git(args, cwd=cwd)

    monkeypatch.setattr(mgr, "_run_git", fake_run_git)

    def fake_rmtree(_path: Path, *_args, **_kwargs) -> None:
        err = PermissionError(13, "The process cannot access the file because it is being used by another process")
        err.winerror = 32  # type: ignore[attr-defined]
        raise err

    monkeypatch.setattr("agentos.code_mode.agent_worktree.shutil.rmtree", fake_rmtree)
    monkeypatch.setattr("agentos.code_mode.agent_worktree.time.sleep", lambda _s: None)

    with pytest.raises(WorktreeError) as exc_info:
        mgr.discard_worktree("locked1")
    assert "outro processo" in str(exc_info.value).lower()
