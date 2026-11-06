from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed on this host")


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True, capture_output=True)


def test_git_repo_change_tracking_ignores_preexisting_untracked_and_uploads(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "image.png").write_bytes(b"png")
    (tmp_path / ".orin" / "state").mkdir(parents=True)
    (tmp_path / ".orin" / "state" / "cwd.json").write_text('{"cwd": "."}', encoding="utf-8")
    (tmp_path / "preexisting.txt").write_text("dirty", encoding="utf-8")

    ws = ConversationWorkspace.at_root(tmp_path)
    baseline = ws.begin_change_tracking()
    artifacts = ws.end_change_tracking(baseline)
    assert artifacts == []

    baseline2 = ws.begin_change_tracking()
    (tmp_path / "new_feature.py").write_text("x = 1\n", encoding="utf-8")
    artifacts2 = ws.end_change_tracking(baseline2)
    paths = [a["path"] for a in artifacts2]
    assert paths == ["new_feature.py"]


def test_git_repo_change_tracking_detects_further_modification_to_preexisting_dirty_file(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    untouched = tmp_path / "untouched_dirty.txt"
    untouched.write_text("stay same", encoding="utf-8")
    modified = tmp_path / "already_dirty.py"
    modified.write_text("x = 1\n", encoding="utf-8")

    ws = ConversationWorkspace.at_root(tmp_path)
    baseline = ws.begin_change_tracking()

    # Modify already_dirty.py so its size/mtime changes
    modified.write_text("x = 1\ny = 2\n", encoding="utf-8")
    stat = modified.stat()
    os.utime(modified, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    artifacts = ws.end_change_tracking(baseline)
    paths = [a["path"] for a in artifacts]
    assert paths == ["already_dirty.py"]


def test_git_repo_change_tracking_ignores_excluded_directories_even_when_created_mid_command(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    ws = ConversationWorkspace.at_root(tmp_path)
    baseline = ws.begin_change_tracking()

    for rel in (
        ".orin/logs/out.txt",
        "uploads/photo.jpg",
        "node_modules/pkg/index.js",
        ".venv/pyvenv.cfg",
        "__pycache__/app.cpython-312.pyc",
    ):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ignored", encoding="utf-8")

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / ".eslintrc.json").write_text("{}\n", encoding="utf-8")

    artifacts = ws.end_change_tracking(baseline)
    paths = [a["path"] for a in artifacts]
    assert paths == [".eslintrc.json", "src/real.py"]


def test_agent_tools_record_changed_paths_ignores_uploads_and_does_not_block_completion_gate(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "image.png").write_bytes(b"png-bytes")
    (tmp_path / "README.md").write_text("# Hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    ws = ConversationWorkspace.at_root(tmp_path)
    tools = AgentToolset(ws, code_mode_active=True, code_mode_work_kind="implementation")

    # Even if artifacts contain uploads/image.png, _record_changed_paths must ignore it
    assert tools._record_changed_paths([{"path": "uploads/image.png"}, {"path": ".orin/state/cwd.json"}]) is False
    assert tools._changed_paths == set()

    # Running a read-only shell command in a repo with untracked uploads/image.png must not record changes
    outcome = tools.invoke("run_command", {"command": "git status --short"})
    assert outcome.status == "succeeded"
    assert tools._changed_paths == set()
    assert tools.change_events() == 0
    assert tools.code_verification_status() == "not_applicable"


def test_change_tracking_across_git_init_and_deleted_file_recreation(tmp_path: Path) -> None:
    (tmp_path / "existing.py").write_text("x = 1\n", encoding="utf-8")
    ws = ConversationWorkspace.at_root(tmp_path)

    # Start before git init (uses file_snapshot baseline)
    baseline = ws.begin_change_tracking()
    _init_git_repo(tmp_path)
    (tmp_path / "created_after_init.py").write_text("y = 2\n", encoding="utf-8")

    artifacts = ws.end_change_tracking(baseline)
    assert [a["path"] for a in artifacts] == ["created_after_init.py"]

    # Commit both, then delete existing.py before next tracking window
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit all"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "existing.py").unlink()

    baseline_deleted = ws.begin_change_tracking()
    assert ws.end_change_tracking(baseline_deleted) == []

    # Recreating the deleted file during a command reports it
    (tmp_path / "existing.py").write_text("x = 99\n", encoding="utf-8")
    artifacts_recreated = ws.end_change_tracking(baseline_deleted)
    assert [a["path"] for a in artifacts_recreated] == ["existing.py"]

