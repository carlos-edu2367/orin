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
