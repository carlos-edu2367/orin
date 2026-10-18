from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentos.code_mode.git_context import git_status_summary

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.test")
    _git(root, "config", "user.name", "t")
    (root / "new.txt").write_text("x", encoding="utf-8")
    return root


def test_no_repository_means_no_status(tmp_path: Path) -> None:
    assert git_status_summary(tmp_path) == ""


@needs_git
def test_a_repository_reports_branch_and_changes(tmp_path: Path) -> None:
    summary = git_status_summary(_repo(tmp_path))
    assert summary.startswith("## ") and "?? new.txt" in summary


@needs_git
def test_the_status_is_bounded(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    for index in range(60):
        (root / f"f{index}.txt").write_text("x", encoding="utf-8")
    summary = git_status_summary(root, max_lines=10)
    assert len(summary.splitlines()) == 11 and summary.splitlines()[-1].startswith("... ")


@needs_git
def test_a_repository_fsmonitor_hook_never_runs(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "hook.py"
    hook.write_text(f"open(r'{marker}', 'w').write('x')\n", encoding="utf-8")
    _git(root, "config", "core.fsmonitor", f'"{sys.executable}" "{hook}"')
    subprocess.run(["git", "status"], cwd=root, capture_output=True)
    if not marker.exists():
        pytest.skip("this git does not run fsmonitor hooks, nothing to prove")
    marker.unlink()
    git_status_summary(root)
    assert not marker.exists()
