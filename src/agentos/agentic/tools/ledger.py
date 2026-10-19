"""Where every Claude Code tool stands in Orin (spec §7: "each one included with a test, or excluded with a reason").

``included`` names the Orin key that implements it (its canonical name is the Claude Code name); ``deferred``
names the wave that brings it; ``excluded`` says why Orin will not have it. The contract test checks that every
included tool is registered under that name with a public input model.
"""
from __future__ import annotations

from typing import Literal, NamedTuple


class LedgerEntry(NamedTuple):
    status: Literal["included", "deferred", "excluded"]
    detail: str  # the Orin key, the wave, or the reason


def _in(key: str) -> LedgerEntry:
    return LedgerEntry("included", key)


def _later(wave: str) -> LedgerEntry:
    return LedgerEntry("deferred", wave)


def _out(reason: str) -> LedgerEntry:
    return LedgerEntry("excluded", reason)


_ANTHROPIC = "an Anthropic product or account feature"

TOOL_LEDGER: dict[str, LedgerEntry] = {
    "Agent": _in("run_subagent"),
    "Bash": _in("run_command"),
    "ExitPlanMode": _in("exit_plan_mode"),
    "Edit": _in("edit_file"),
    "Read": _in("read_file"),
    "Write": _in("write_file"),
    "Glob": _in("glob_files"),
    "Grep": _in("search_files"),
    "TaskStop": _in("stop_process"),
    "ListMcpResources": _later("W5"),
    "RefreshMcpTools": _later("W5"),
    "Mcp": _out("Orin publishes each MCP tool under its own mcp__server__tool name"),
    "NotebookEdit": _in("notebook_edit"),
    "ReadMcpResourceDir": _later("W5"),
    "ReadMcpResource": _later("W5"),
    "ReportFindings": _later("W8"),
    "TodoWrite": _in("todo_write"),
    "WebFetch": _in("fetch_url"),
    "WebSearch": _in("web_search"),
    "AskUserQuestion": _in("ask_user"),
    "SendFeedback": _out(_ANTHROPIC),
    "ClaudeDesign": _out(_ANTHROPIC),
    "Projects": _out("Orin has its own project tools (ListProjects, CreateProjectFromSession, ...)"),
    "EnterPlanMode": _in("enter_plan_mode"),
    "TaskCreate": _in("task_create"),
    "TaskGet": _in("task_get"),
    "TaskUpdate": _in("task_update"),
    "TaskList": _in("task_list"),
    "Workflow": _out(_ANTHROPIC),
    "CronCreate": _in("cron_create"),
    "CronDelete": _in("cron_delete"),
    "CronList": _in("cron_list"),
    "ScheduleWakeup": _in("schedule_wakeup"),
    "RemoteTrigger": _out(_ANTHROPIC),
    "ShowOnboardingRolePicker": _out(_ANTHROPIC),
    "OfferChromeSetup": _out(_ANTHROPIC),
    "ReadNotifications": _out(_ANTHROPIC),
    "Monitor": _in("monitor"),
    "ProposeSkills": _out("Orin creates and edits skills with its own CreateSkill and EditSkill tools"),
    "ProposeGoal": _out(_ANTHROPIC),
    "Artifact": _out(_ANTHROPIC),
    "PushNotification": _in("push_notification"),
    "EnterWorktree": _in("enter_worktree"),
    "ExitWorktree": _in("exit_worktree"),
    # Not in the SDK's tool input list, but Claude Code tools all the same.
    "PowerShell": _in("powershell"),
    "TaskOutput": _in("read_process_output"),
    "ToolSearch": _in("search_mcp_tools"),
    "Skill": _in("use_skill"),
    "SendMessage": _later("W5"),
}

__all__ = ["LedgerEntry", "TOOL_LEDGER"]
