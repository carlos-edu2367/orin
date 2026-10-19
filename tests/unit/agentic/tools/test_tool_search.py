"""W4: ToolSearch's select: and +term forms."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset, ToolDefinition
from agentos.agentic.tools.mcp.admin import ToolSearchOutput
from agentos.agentic.workspace import ConversationWorkspace


class _Provider:
    def definitions(self):
        names = ("mcp__github__create_issue", "mcp__github__list_issues", "mcp__slack__send_message")
        return tuple(
            ToolDefinition(name, f"{name} description", {"type": "object", "properties": {}}, lambda **_: {"content": "ok"}, "mcp")
            for name in names
        )


def _toolset(tmp_path: Path) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "c1"), mcp_provider=_Provider())


def test_select_loads_exactly_the_named_tools(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    outcome = toolset.invoke("ToolSearch", {"query": "select:mcp__github__create_issue,SEND_MESSAGE,Read,nope"})
    output = outcome.data["tool_output"]
    ToolSearchOutput.model_validate(output)
    assert output["matches"] == ["mcp__github__create_issue", "mcp__slack__send_message"]
    assert output["already_available"] == ["Read"] and output["not_found"] == ["nope"]
    assert {"mcp__github__create_issue", "mcp__slack__send_message"} <= toolset.active_mcp_tool_names


def test_plus_terms_are_required_in_the_name(tmp_path: Path) -> None:
    output = _toolset(tmp_path).invoke("ToolSearch", {"query": "+github issue"}).data["tool_output"]
    assert output["matches"] and all("github" in name for name in output["matches"])
