"""W4: the ledger of every Claude Code tool — included and registered, deferred to a wave, or excluded with a reason."""
from __future__ import annotations

import re

from agentos.agentic.tools.ledger import TOOL_LEDGER
from agentos.agentic.tools.public_names import PUBLIC_NAMES
from agentos.agentic.tools.registry import default_registry

SDK_TOOLS = (
    "Agent", "Bash", "ExitPlanMode", "Edit", "Read", "Write", "Glob", "Grep", "TaskStop", "ListMcpResources",
    "RefreshMcpTools", "Mcp", "NotebookEdit", "ReadMcpResourceDir", "ReadMcpResource", "ReportFindings", "TodoWrite",
    "WebFetch", "WebSearch", "AskUserQuestion", "SendFeedback", "ClaudeDesign", "Projects", "EnterPlanMode", "TaskCreate",
    "TaskGet", "TaskUpdate", "TaskList", "Workflow", "CronCreate", "CronDelete", "CronList", "ScheduleWakeup",
    "RemoteTrigger", "ShowOnboardingRolePicker", "OfferChromeSetup", "ReadNotifications", "Monitor", "ProposeSkills",
    "ProposeGoal", "Artifact", "PushNotification", "EnterWorktree", "ExitWorktree",
)
OTHER_TOOLS = ("PowerShell", "TaskOutput", "ToolSearch", "Skill", "SendMessage")


def test_every_claude_code_tool_has_exactly_one_entry() -> None:
    assert len(SDK_TOOLS) == 44 and len(set(SDK_TOOLS)) == 44
    assert set(TOOL_LEDGER) == set(SDK_TOOLS) | set(OTHER_TOOLS)


def test_included_tools_are_registered_under_their_claude_code_name() -> None:
    registry = default_registry()
    for name, entry in TOOL_LEDGER.items():
        if entry.status != "included":
            continue
        spec = registry.get(entry.detail)
        assert spec is not None, name
        assert PUBLIC_NAMES.get(entry.detail) == name, name
        assert spec.input_model is not None, f"{name} has no public input model"


def test_deferred_tools_name_a_wave_and_excluded_ones_a_reason() -> None:
    for name, entry in TOOL_LEDGER.items():
        if entry.status == "deferred":
            assert re.fullmatch(r"W[5-9]", entry.detail), name
        elif entry.status == "excluded":
            assert len(entry.detail) >= 20, name
        else:
            assert entry.status == "included", name


def test_the_counts_are_what_the_w4_ledger_reports() -> None:
    counts = {status: sum(1 for entry in TOOL_LEDGER.values() if entry.status == status) for status in ("included", "deferred", "excluded")}
    assert counts == {"included": 36, "deferred": 1, "excluded": 12}
