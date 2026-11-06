from __future__ import annotations

import pytest

from agentos.agentic.agent_tools import AgentToolset, ConversationWorkspace


@pytest.fixture
def workspace(tmp_path) -> ConversationWorkspace:
    return ConversationWorkspace(tmp_path, "c1")


@pytest.fixture
def toolset(workspace: ConversationWorkspace) -> AgentToolset:
    return AgentToolset(workspace, code_mode_active=True, permission_mode="full_autonomy", public_tool_names=True)


def test_default_read_returns_up_to_2000_lines(toolset: AgentToolset, workspace: ConversationWorkspace) -> None:
    workspace.write_text("big.txt", "".join(f"l{i}\n" for i in range(1, 2501)))
    out = toolset.invoke("Read", {"file_path": "big.txt"}).data["tool_output"]["file"]
    assert out["numLines"] == 2000 and out["startLine"] == 1


def test_token_budget_cuts_whole_lines_and_points_to_the_next_offset(
    toolset: AgentToolset, workspace: ConversationWorkspace
) -> None:
    workspace.write_text("wide.txt", "".join("x" * 400 + "\n" for _ in range(2000)))
    outcome = toolset.invoke("Read", {"file_path": "wide.txt"})
    shown = outcome.data["tool_output"]["file"]["numLines"]
    assert shown < 2000 and len(outcome.content) // 4 <= 25_000
    assert f"offset={shown + 1}" in outcome.content
    assert "output truncated at" not in outcome.content


def test_a_giant_single_line_is_truncated_and_recorded_as_read(
    toolset: AgentToolset, workspace: ConversationWorkspace
) -> None:
    workspace.write_text("min.js", "a" * 300_000)
    outcome = toolset.invoke("Read", {"file_path": "min.js"})
    assert "[line truncated]" in outcome.content and len(outcome.content) < 4_000
    assert (
        toolset.invoke("Edit", {"file_path": "min.js", "old_string": "aaa", "new_string": "b", "replace_all": False}).error_code
        != "FILE_NOT_READ"
    )


def test_offset_past_the_first_200kb_is_readable(toolset: AgentToolset, workspace: ConversationWorkspace) -> None:
    workspace.write_text("log.txt", "".join(f"{i:08d}" + "y" * 100 + "\n" for i in range(4000)))
    out = toolset.invoke("Read", {"file_path": "log.txt", "offset": 3990}).data["tool_output"]["file"]
    assert out["startLine"] == 3990 and out["numLines"] == 11
