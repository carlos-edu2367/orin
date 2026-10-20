"""W7 Batch F (W4): ExitPlanMode's filePath is a path the workspace resolved, never a raw join."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace


def _toolset(tmp_path: Path) -> AgentToolset:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, permission_mode="plan_mode")
    toolset.invoke("EnterPlanMode", {})
    return toolset


def test_a_plan_path_outside_the_workspace_gives_no_file_path(tmp_path: Path) -> None:
    outcome = _toolset(tmp_path).invoke("exit_plan_mode", {"plan_path": "../../../Windows/win.ini", "summary": "pronto"})
    assert outcome.status == "succeeded", outcome.content
    assert "filePath" not in outcome.data["tool_output"]


def test_a_plan_file_in_the_workspace_is_reported_as_the_workspace_resolved_it(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    plan = toolset.workspace.root / ".orin" / "plans" / "billing.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("# Plano\n1. Ajustar a taxa", encoding="utf-8")
    outcome = toolset.invoke("exit_plan_mode", {"plan_path": ".orin/plans/billing.md", "summary": "pronto"})
    output = outcome.data["tool_output"]
    assert Path(output["filePath"]) == toolset.workspace.resolve(".orin/plans/billing.md")
    assert "Ajustar a taxa" in output["plan"]
