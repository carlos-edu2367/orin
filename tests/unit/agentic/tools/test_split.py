"""W4: AgentToolset is split into per-family modules; the pipeline stays in agent_tools."""
from __future__ import annotations

import logging

from agentos.agentic import agent_tools


def test_shared_helpers_live_in_their_own_module() -> None:
    from agentos.agentic.tools import _shared

    for name in (
        "ToolDefinition", "ToolOutcome", "AgentToolError", "ReadFileRecord", "compute_structured_patch",
        "coerce_tool_arguments", "MAX_TOOL_RESULT_CHARS", "_schema", "_TEXT", "sanitize_page_text",
        "_terminate_process_tree", "_process_is_running",
    ):
        assert getattr(agent_tools, name) is getattr(_shared, name), name
    assert agent_tools.ToolOutcome.__module__ == "agentos.agentic.tools._shared"


def test_the_logger_keeps_its_name() -> None:
    from agentos.agentic.tools import _shared

    assert _shared.logger is logging.getLogger("agentos.agentic.agent_tools")


import importlib
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset

FAMILIES = [
    ("files.read", "ReadTools", ("read_file", "view_file", "transcribe_pdf", "detect_external_changes")),
    ("files.write", "WriteTools", ("write_file",)),
    ("files.edit", "EditTools", ("edit_file", "_normalized_edits")),
    ("files.notebook", "NotebookTools", ("notebook_edit",)),
    ("search.glob", "GlobTools", ("glob_files", "list_files", "_glob_matches_path")),
    ("search.grep", "GrepTools", ("search_files",)),
    ("orin.search_code", "CodeSearchTools", ("search_code", "project_map")),
    ("shell.bash", "BashTools", ("run_command", "_resolve_cwd_after_command")),
    ("shell.tasks", "ShellTaskTools", ("read_process_output", "stop_process")),
    ("orin.verify", "VerifyTools", ("verify_project", "report_verification")),
    ("web.fetch", "FetchTools", ("fetch_url",)),
    ("web.search", "WebSearchTools", ("web_search",)),
    ("orin.memory", "MemoryTools", ("remember", "recall")),
    ("orin.skills_admin", "SkillTools", ("use_skill", "search_skills", "create_skill")),
    ("mcp.admin", "McpTools", ("search_mcp_tools", "call_mcp_tool", "configure_mcp")),
    ("orin.plugins_admin", "PluginTools", ("install_plugin", "list_plugins")),
    ("orin.projects", "ProjectTools", ("list_projects", "propose_code_session")),
    ("planning.todo", "TodoTools", ("todo_write", "complete_remaining_todos")),
    ("planning.tasks", "TaskTools", ("task_create", "task_update", "task_get", "task_list")),
    ("planning.plan_mode", "PlanModeTools", ("enter_plan_mode", "exit_plan_mode", "write_contract")),
    ("interaction.ask_user", "AskUserTools", ("ask_user",)),
    ("agents.agent", "AgentTools", ("run_subagent", "check_subagent", "ask_agents")),
    ("git.worktree", "WorktreeTools", ("enter_worktree", "exit_worktree")),
    ("orin.preview", "PreviewTools", ("preview_start", "preview_stop", "preview_logs")),
    ("orin.media", "MediaTools", ("generate_image", "generate_audio", "generate_video")),
]


@pytest.mark.parametrize(("module", "mixin", "methods"), FAMILIES)
def test_each_family_lives_in_its_module(module: str, mixin: str, methods: tuple[str, ...]) -> None:
    family = getattr(importlib.import_module(f"agentos.agentic.tools.{module}"), mixin)
    assert issubclass(AgentToolset, family)
    for method in methods:
        assert method in vars(family), f"{mixin}.{method}"
        assert method not in vars(AgentToolset), f"AgentToolset still defines {method}"


def test_agent_tools_keeps_only_the_pipeline() -> None:
    lines = Path(agent_tools.__file__).read_text(encoding="utf-8").count("\n")
    assert lines < 1700, lines
