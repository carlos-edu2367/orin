"""TDD tests for WS-B2: MergeQueue (B-RF2)."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.code_mode.agent_worktree import AgentWorktreeManager
from agentos.code_mode.merge_queue import MergeQueue, MergeResult
from agentos.persistence.postgres.agent_worktrees import AgentWorktreeStore
from agentos.persistence.postgres.schema import metadata


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "orin-test@example.com")
    _git(repo, "config", "user.name", "Orin Test")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / ".gitignore").write_text(".orin/\nnode_modules/\n.venv/\n", encoding="utf-8")
    (repo / "shared.txt").write_text("line 1\nline 2\nline 3\n", encoding="utf-8")
    (repo / "alpha.txt").write_text("alpha v1\n", encoding="utf-8")
    (repo / "beta.txt").write_text("beta v1\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "shared.txt", "alpha.txt", "beta.txt")
    _git(repo, "commit", "-m", "initial commit")
    return repo


def _make_store() -> AgentWorktreeStore:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    return AgentWorktreeStore(engine)


def _tree_hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".git/") or rel.startswith(".orin/"):
            continue
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_criterion_2_two_parallel_children_different_files_apply_without_commits(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo-parallel")
    store = _make_store()
    mgr = AgentWorktreeManager(repo, store=store)
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    wt1 = mgr.create_for_agent("child1")
    wt2 = mgr.create_for_agent("child2")

    # Child 1 edits alpha.txt and creates a brand-new file new_from_child1.txt.
    (wt1.path / "alpha.txt").write_text("alpha v2 from child1\n", encoding="utf-8")
    (wt1.path / "new_from_child1.txt").write_text("hello new file\n", encoding="utf-8")

    # Child 2 edits beta.txt.
    (wt2.path / "beta.txt").write_text("beta v2 from child2\n", encoding="utf-8")

    queue = MergeQueue(repo, manager=mgr, store=store)
    r1 = queue.enqueue("child1")
    r2 = queue.enqueue("child2")

    assert isinstance(r1, MergeResult)
    assert r1.state == "applied"
    assert set(r1.files) == {"alpha.txt", "new_from_child1.txt"}
    assert r2.state == "applied"
    assert set(r2.files) == {"beta.txt"}

    # Both applied in parent tree, zero commits created, both worktrees & branches removed.
    assert (repo / "alpha.txt").read_text(encoding="utf-8") == "alpha v2 from child1\n"
    assert (repo / "new_from_child1.txt").read_text(encoding="utf-8") == "hello new file\n"
    assert (repo / "beta.txt").read_text(encoding="utf-8") == "beta v2 from child2\n"
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before
    assert not wt1.path.exists()
    assert not wt2.path.exists()
    assert _git(repo, "branch", "--list", "orin/agent-*").stdout.strip() == ""
    assert store.get("child1").merge_state == "applied"
    assert store.get("child2").merge_state == "applied"


def test_criterion_3_same_line_conflict_leaves_parent_clean_and_keeps_second_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo-conflict")
    store = _make_store()
    mgr = AgentWorktreeManager(repo, store=store)

    wt1 = mgr.create_for_agent("c1")
    wt2 = mgr.create_for_agent("c2")

    (wt1.path / "shared.txt").write_text("line 1\nEDITED BY C1\nline 3\n", encoding="utf-8")
    (wt2.path / "shared.txt").write_text("line 1\nEDITED BY C2\nline 3\n", encoding="utf-8")
    (wt2.path / "extra_c2.txt").write_text("should not be applied on conflict\n", encoding="utf-8")

    queue = MergeQueue(repo, manager=mgr, store=store)
    r1 = queue.enqueue("c1")
    assert r1.state == "applied"
    parent_after_c1 = _tree_hashes(repo)

    r2 = queue.enqueue("c2")
    assert r2.state == "conflict"
    assert "shared.txt" in r2.files
    assert r2.conflict_detail is not None
    assert r2.worktree_path == str(wt2.path)

    # Nothing from child 2 was applied to the parent tree (exact hash match)!
    assert _tree_hashes(repo) == parent_after_c1
    assert not (repo / "extra_c2.txt").exists()
    assert (repo / "shared.txt").read_text(encoding="utf-8") == "line 1\nEDITED BY C1\nline 3\n"
    # Second worktree and branch remain intact for inspection/discard/rebase.
    assert wt2.path.is_dir()
    assert _git(repo, "branch", "--list", wt2.branch).stdout.strip() != ""
    assert store.get("c2").merge_state == "conflict"


def test_criterion_4_merge_check_command_failure_reverts_with_exact_hashes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo-check-fail")
    store = _make_store()
    mgr = AgentWorktreeManager(repo, store=store)

    before_hashes = _tree_hashes(repo)
    wt = mgr.create_for_agent("chk1")
    (wt.path / "alpha.txt").write_text("alpha mutated\n", encoding="utf-8")
    (wt.path / "created_by_child.txt").write_text("brand new\n", encoding="utf-8")

    cmd = f'"{sys.executable}" -c "import sys; sys.exit(1)"'
    queue = MergeQueue(
        repo,
        manager=mgr,
        store=store,
        merge_check_command=cmd,
        command_authorizer=lambda _c: (True, "allowed"),
    )
    res = queue.enqueue("chk1")
    assert res.state == "check_failed"
    assert _tree_hashes(repo) == before_hashes
    assert not (repo / "created_by_child.txt").exists()
    assert wt.path.is_dir()
    assert store.get("chk1").merge_state == "check_failed"


def test_merge_check_command_denied_by_permissions_does_not_run_and_reverts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo-check-denied")
    store = _make_store()
    mgr = AgentWorktreeManager(repo, store=store)

    before_hashes = _tree_hashes(repo)
    marker = tmp_path / "ran.marker"
    wt = mgr.create_for_agent("deny1")
    (wt.path / "alpha.txt").write_text("alpha mutated\n", encoding="utf-8")

    cmd = f'"{sys.executable}" -c "from pathlib import Path; Path({str(marker)!r}).write_text(\'ran\')"'
    queue = MergeQueue(
        repo,
        manager=mgr,
        store=store,
        merge_check_command=cmd,
        command_authorizer=lambda _c: (False, "denied by permission policy"),
    )
    res = queue.enqueue("deny1")
    assert res.state == "check_failed"
    assert not marker.exists()
    assert _tree_hashes(repo) == before_hashes
    assert store.get("deny1").merge_state == "check_failed"


def test_merge_check_cancelled_midway_reverts_exact_bytes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo-check-cancel")
    mgr = AgentWorktreeManager(repo)
    before_hashes = _tree_hashes(repo)

    wt = mgr.create_for_agent("cancel1")
    (wt.path / "alpha.txt").write_text("alpha cancelled\n", encoding="utf-8")

    cancel_flag = threading.Event()
    cmd = f'"{sys.executable}" -c "import time; time.sleep(5)"'

    def trigger_cancel() -> None:
        time.sleep(0.2)
        cancel_flag.set()

    t = threading.Thread(target=trigger_cancel, daemon=True)
    t.start()

    queue = MergeQueue(
        repo,
        manager=mgr,
        merge_check_command=cmd,
        command_authorizer=lambda _c: (True, "allowed"),
    )
    res = queue.enqueue("cancel1", cancelled=cancel_flag.is_set)
    t.join(timeout=2)

    assert res.state == "check_failed"
    assert _tree_hashes(repo) == before_hashes


def test_dirty_parent_tree_tolerated_by_3way_apply_and_untouched_untracked_not_reapplied(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo-dirty-3way")
    # Parent has both a dirty tracked edit on alpha.txt and an untracked file.
    (repo / "alpha.txt").write_text("alpha dirty parent\n", encoding="utf-8")
    (repo / "parent_note.txt").write_text("parent note v1\n", encoding="utf-8")

    mgr = AgentWorktreeManager(repo)
    wt = mgr.create_for_agent("dirty3way")

    # Parent continues editing parent_note.txt and shared.txt while child runs.
    (repo / "parent_note.txt").write_text("parent note v2 changed by parent\n", encoding="utf-8")
    (repo / "shared.txt").write_text("line 1\nline 2\nline 3 parent\n", encoding="utf-8")

    # Child only edits beta.txt (leaves copied parent_note.txt untouched).
    (wt.path / "beta.txt").write_text("beta edited by child\n", encoding="utf-8")

    queue = MergeQueue(repo, manager=mgr)
    res = queue.enqueue("dirty3way")
    assert res.state == "applied"
    assert res.files == ("beta.txt",)
    assert (repo / "beta.txt").read_text(encoding="utf-8") == "beta edited by child\n"
    # Parent's dirty edits and updated untracked file were preserved untouched!
    assert (repo / "alpha.txt").read_text(encoding="utf-8") == "alpha dirty parent\n"
    assert (repo / "parent_note.txt").read_text(encoding="utf-8") == "parent note v2 changed by parent\n"


def test_crlf_and_spaces_accents_file_merges_cleanly(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repositório revisão espaço")
    crlf_path = repo / "pasta ação" / "win_file.txt"
    crlf_path.parent.mkdir(parents=True)
    crlf_path.write_bytes(b"first\r\nsecond\r\nthird\r\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add crlf accented file")

    mgr = AgentWorktreeManager(repo)
    wt = mgr.create_for_agent("crlf1")
    (wt.path / "pasta ação" / "win_file.txt").write_bytes(b"first\r\nsecond edited\r\nthird\r\n")

    queue = MergeQueue(repo, manager=mgr)
    res = queue.enqueue("crlf1")
    assert res.state == "applied"
    assert crlf_path.read_bytes() == b"first\r\nsecond edited\r\nthird\r\n"


def test_concurrent_enqueue_serializes_git_apply_with_barrier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path / "repo-serial")
    mgr = AgentWorktreeManager(repo)
    wt1 = mgr.create_for_agent("s1")
    wt2 = mgr.create_for_agent("s2")
    (wt1.path / "alpha.txt").write_text("alpha serial\n", encoding="utf-8")
    (wt2.path / "beta.txt").write_text("beta serial\n", encoding="utf-8")

    queue = MergeQueue(repo, manager=mgr)
    barrier = threading.Barrier(2, timeout=5)
    active_applies = 0
    max_concurrent = 0
    counter_lock = threading.Lock()
    orig_apply = queue._apply_patch_3way

    def tracked_apply(*args, **kwargs):
        nonlocal active_applies, max_concurrent
        with counter_lock:
            active_applies += 1
            max_concurrent = max(max_concurrent, active_applies)
        time.sleep(0.15)
        try:
            return orig_apply(*args, **kwargs)
        finally:
            with counter_lock:
                active_applies -= 1

    monkeypatch.setattr(queue, "_apply_patch_3way", tracked_apply)

    results: dict[str, MergeResult] = {}

    def worker(aid: str) -> None:
        barrier.wait()
        results[aid] = queue.enqueue(aid)

    t1 = threading.Thread(target=worker, args=("s1",))
    t2 = threading.Thread(target=worker, args=("s2",))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert max_concurrent == 1
    assert results["s1"].state == "applied"
    assert results["s2"].state == "applied"
    assert (repo / "alpha.txt").read_text(encoding="utf-8") == "alpha serial\n"
    assert (repo / "beta.txt").read_text(encoding="utf-8") == "beta serial\n"
