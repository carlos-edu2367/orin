from __future__ import annotations

from pathlib import Path

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
