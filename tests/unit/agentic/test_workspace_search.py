from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError


@pytest.fixture()
def workspace(tmp_path: Path) -> ConversationWorkspace:
    return ConversationWorkspace(tmp_path, "chat_search")


def test_search_finds_matches_with_path_and_line_number(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/app.py", "import os\nDEBUG = True\n")
    workspace.write_text("docs/readme.md", "nothing here\n")

    results = workspace.search("DEBUG")

    assert results == [{"path": "src/app.py", "line": 2, "text": "DEBUG = True"}]


def test_search_respects_the_glob_filter(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/app.py", "target\n")
    workspace.write_text("docs/readme.md", "target\n")

    results = workspace.search("target", glob="**/*.md")

    assert [item["path"] for item in results] == ["docs/readme.md"]


def test_search_is_case_insensitive_by_default_and_can_be_made_exact(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/app.py", "Debug\n")

    assert workspace.search("debug")
    assert workspace.search("debug", ignore_case=False) == []


def test_search_caps_the_number_of_results(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/app.py", "hit\n" * 40)

    assert len(workspace.search("hit", max_results=5)) == 5


def test_search_rejects_an_invalid_regular_expression(workspace: ConversationWorkspace) -> None:
    with pytest.raises(WorkspaceError):
        workspace.search("[unclosed")


def test_search_rejects_a_glob_that_tries_to_escape_the_workspace(workspace: ConversationWorkspace) -> None:
    with pytest.raises(WorkspaceError):
        workspace.search("anything", glob="../**/*")


def test_read_lines_returns_a_window_and_the_total(workspace: ConversationWorkspace) -> None:
    workspace.write_text("big.txt", "\n".join(f"line {index}" for index in range(1, 101)) + "\n")

    lines, first, total, truncated = workspace.read_lines("big.txt", offset=10, limit=3)

    assert lines == ["line 10", "line 11", "line 12"]
    assert (first, total, truncated) == (10, 100, False)


def test_read_lines_clamps_an_offset_past_the_end(workspace: ConversationWorkspace) -> None:
    workspace.write_text("small.txt", "only\n")

    lines, first, total, _ = workspace.read_lines("small.txt", offset=99)

    assert lines == []
    assert (first, total) == (99, 1)


def test_read_lines_rejects_a_non_file(workspace: ConversationWorkspace) -> None:
    with pytest.raises(WorkspaceError):
        workspace.read_lines("missing.txt")


def test_list_entries_is_shallow_by_default(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/deep/app.py", "x\n")

    paths = [item["path"] for item in workspace.list_entries()]

    assert paths == ["src"]


def test_list_entries_descends_when_asked(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/deep/app.py", "x\n")

    paths = [item["path"] for item in workspace.list_entries(depth=3)]

    assert paths == ["src", "src/deep", "src/deep/app.py"]


def test_list_entries_does_not_follow_a_symlink_out_of_the_workspace(
    workspace: ConversationWorkspace, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        (workspace.root / "linked").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")

    results = workspace.list_entries(depth=3)

    assert not any(item["path"].endswith("secret.txt") for item in results)


def test_list_entries_hides_dependency_and_build_directories(workspace: ConversationWorkspace) -> None:
    workspace.write_text("node_modules/react/index.js", "module.exports = {}\n")
    workspace.write_text("src/app.py", "x\n")

    paths = [item["path"] for item in workspace.list_entries(depth=3)]

    assert "src" in paths
    assert not any(path.startswith("node_modules") for path in paths)


def test_list_entries_honours_the_workspace_gitignore(workspace: ConversationWorkspace) -> None:
    workspace.write_text(".gitignore", "generated/\n")
    workspace.write_text("generated/output.txt", "x\n")
    workspace.write_text("src/app.py", "x\n")

    paths = [item["path"] for item in workspace.list_entries(depth=3)]

    assert "src" in paths
    assert not any(path.startswith("generated") for path in paths)


def test_search_ignores_dependency_directories(workspace: ConversationWorkspace) -> None:
    workspace.write_text("node_modules/react/index.js", "DEBUG\n")
    workspace.write_text("src/app.py", "DEBUG\n")

    results = workspace.search("DEBUG")

    assert [item["path"] for item in results] == ["src/app.py"]


def test_glob_lists_matching_paths_and_hides_dependency_directories(workspace: ConversationWorkspace) -> None:
    workspace.write_text("src/app.tsx", "x\n")
    workspace.write_text("src/app.py", "x\n")
    workspace.write_text("node_modules/pkg/index.tsx", "x\n")

    assert workspace.glob("**/*.tsx") == ["src/app.tsx"]


def test_glob_rejects_a_pattern_that_tries_to_escape_the_workspace(workspace: ConversationWorkspace) -> None:
    with pytest.raises(WorkspaceError):
        workspace.glob("../**/*")


def test_file_snapshot_does_not_spend_its_cap_on_dependency_files(workspace: ConversationWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    import agentos.agentic.workspace as workspace_module

    monkeypatch.setattr(workspace_module, "MAX_SNAPSHOT_FILES", 2)
    for index in range(5):
        workspace.write_text(f"node_modules/pkg/file{index}.js", "x\n")
    workspace.write_text("src/app.py", "x\n")

    snapshot = workspace.file_snapshot()

    assert "src/app.py" in snapshot
    assert not any(path.startswith("node_modules") for path in snapshot)


def test_prepare_write_target_creates_parent_directories(workspace: ConversationWorkspace) -> None:
    target = workspace.prepare_write_target(".orin/logs/proc.log")

    assert target.parent.is_dir()
    assert target.parent == workspace.root / ".orin" / "logs"


def test_prepare_write_target_rejects_the_workspace_root(workspace: ConversationWorkspace) -> None:
    with pytest.raises(WorkspaceError):
        workspace.prepare_write_target("")


def test_change_tracking_reports_a_file_written_in_between(workspace: ConversationWorkspace) -> None:
    workspace.write_text("existing.txt", "before\n")

    baseline = workspace.begin_change_tracking()
    workspace.write_text("new.txt", "after\n")
    changed = workspace.end_change_tracking(baseline)

    assert {"path": "new.txt", "size_bytes": 6} in changed
    assert not any(item["path"] == "existing.txt" for item in changed)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed on this host")
def test_change_tracking_uses_git_status_when_the_workspace_is_a_repo(workspace: ConversationWorkspace) -> None:
    subprocess.run(["git", "init", "-q"], cwd=workspace.root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=workspace.root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=workspace.root, check=True)
    workspace.write_text("tracked.txt", "before\n")
    subprocess.run(["git", "add", "-A"], cwd=workspace.root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=workspace.root, check=True)

    baseline = workspace.begin_change_tracking()
    assert baseline is None  # git can answer without a tree walk

    workspace.write_text("new.txt", "created\n")
    changed = workspace.end_change_tracking(baseline)

    assert {"path": "new.txt", "size_bytes": 8} in changed
    assert not any(item["path"] == "tracked.txt" for item in changed)
