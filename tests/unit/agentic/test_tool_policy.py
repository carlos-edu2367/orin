from __future__ import annotations

from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tool_policy import AllowList
from agentos.agentic.workspace import ConversationWorkspace


def _tools(tmp_path: Path, policy) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "chat_policy"), policy=policy)


def test_a_denied_tool_is_not_published_to_the_model(tmp_path: Path) -> None:
    tools = _tools(tmp_path, AllowList(denied=("run_command",)))

    assert "run_command" not in [item.name for item in tools.definitions()]
    assert "read_file" in [item.name for item in tools.definitions()]


def test_a_denied_tool_is_refused_even_if_the_model_calls_it_anyway(tmp_path: Path) -> None:
    tools = _tools(tmp_path, AllowList(denied=("run_command",)))

    outcome = tools.invoke("run_command", {"command": "echo hi"})

    assert outcome.status == "failed"
    assert outcome.error_code == "UNKNOWN_TOOL"


def test_an_allow_list_publishes_only_what_it_names(tmp_path: Path) -> None:
    tools = _tools(tmp_path, AllowList(allowed=("read_file", "list_files")))

    assert sorted(item.name for item in tools.definitions()) == ["list_files", "read_file"]


def test_a_policy_can_deny_a_whole_family_by_tag(tmp_path: Path) -> None:
    tools = _tools(tmp_path, AllowList(denied=("tag:mutates",)))

    published = [item.name for item in tools.definitions()]
    assert "write_file" not in published
    assert "edit_file" not in published
    assert "read_file" in published


def test_a_mutation_policy_hides_both_single_and_batch_delegation(tmp_path: Path) -> None:
    tools = AgentToolset(
        ConversationWorkspace(tmp_path, "chat_policy"),
        delegate=lambda _name, _task: None,
        delegate_batch=lambda _tasks: None,
        policy=AllowList(denied=("tag:mutates",)),
    )

    published = [item.name for item in tools.definitions()]

    assert "ask_agent" not in published
    assert "ask_agents" not in published


def test_no_policy_publishes_everything(tmp_path: Path) -> None:
    tools = AgentToolset(ConversationWorkspace(tmp_path, "chat_policy"))

    assert "run_command" in [item.name for item in tools.definitions()]
