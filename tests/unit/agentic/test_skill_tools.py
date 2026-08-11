from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.skills.builtins import load_builtin_skills
from agentos.skills.registry import SkillRegistry


def _tools(tmp_path: Path) -> AgentToolset:
    return AgentToolset(
        ConversationWorkspace(tmp_path, "skills_chat"),
        skills=SkillRegistry(load_builtin_skills()),
    )


def test_search_and_list_skills_return_compact_metadata_only(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    searched = tools.invoke("search_skills", {"query": "endpoint returns an error"})
    listed = tools.invoke("list_skills", {"tag": "testing", "limit": 4})

    assert searched.status == "succeeded"
    assert "systematic-debugging" in searched.content
    assert "## Workflow" not in searched.content
    assert "testing" in listed.content


def test_use_skill_loads_instructions_once_without_enabling_new_tools(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    first = tools.invoke("use_skill", {"skill_id": "systematic-debugging"})
    second = tools.invoke("use_skill", {"skill_id": "systematic-debugging"})

    assert first.status == "succeeded"
    assert "## Workflow" in first.content
    assert "ignore" not in first.content.lower()
    assert second.content == "Skill systematic-debugging@1.0.0 is already loaded."
    assert "launch_missiles" not in {item.name for item in tools.definitions()}
