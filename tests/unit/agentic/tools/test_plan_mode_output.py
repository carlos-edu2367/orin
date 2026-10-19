"""W4: ExitPlanMode reports the plan as Claude Code does."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tools.planning.plan_mode import ExitPlanModeOutput
from agentos.agentic.workspace import ConversationWorkspace


def test_exit_plan_mode_reports_the_plan(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, permission_mode="plan_mode")
    toolset.invoke("EnterPlanMode", {})
    outcome = toolset.invoke("ExitPlanMode", {"plan": "# Plan\n1. Do it"})
    assert outcome.status == "succeeded", outcome.content
    output = outcome.data["tool_output"]
    ExitPlanModeOutput.model_validate(output)
    assert output["plan"] and "Do it" in output["plan"] and output["isAgent"] is False
