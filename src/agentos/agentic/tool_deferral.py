from __future__ import annotations

import os
from collections.abc import Mapping

CODE_MODE_CORE_TOOLS: frozenset[str] = frozenset({
    "read_file", "view_file", "transcribe_pdf", "write_file", "edit_file", "notebook_edit",
    "glob_files", "list_files", "search_files", "search_code", "project_map",
    "run_command", "powershell", "read_process_output", "stop_process", "verify_project",
    "enter_worktree", "exit_worktree", "preview_start", "preview_open", "preview_stop", "preview_logs",
    "enter_plan_mode", "exit_plan_mode",
    "todo_write", "task_create", "task_update", "task_get", "task_list",
    "ask_user", "run_subagent", "check_subagent", "fetch_url", "web_search",
    "remember", "recall",
    "search_skills", "list_skills", "read_skill_resource", "use_skill", "create_skill", "edit_skill",
    "list_mcp_catalog", "list_mcp_servers", "configure_mcp", "test_mcp_server",
    "search_mcp_tools", "inspect_mcp_server", "call_mcp_tool",
    "list_mcp_resources", "read_mcp_resource", "read_mcp_resource_dir", "refresh_mcp_tools",
    "search_plugin", "inspect_plugin", "install_plugin", "list_plugins", "uninstall_plugin",
    "list_projects", "create_project_from_session", "move_session_to_project", "read_project_conversation", "search_conversations",
    "generate_image", "generate_audio", "generate_video",
    "cron_create", "cron_delete", "cron_list", "schedule_wakeup", "monitor", "push_notification", "send_message",
})

ALWAYS_LOADED_CODE_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "write_file",
    "edit_file",
    "glob_files",
    "search_files",
    "run_command",
    "todo_write",
    "run_subagent",
    "ask_user",
    "search_code",
    "project_map",
    "verify_project",
    "preview_start",
    "preview_stop",
    "search_mcp_tools",
})

FOLLOW_UP_TOOLS: Mapping[str, tuple[str, ...]] = {
    "run_command": ("read_process_output", "stop_process", "preview_open"),
    "powershell": ("read_process_output", "stop_process", "preview_open"),
    "run_subagent": ("check_subagent",),
    "enter_plan_mode": ("exit_plan_mode",),
    "preview_start": ("preview_logs", "preview_open"),
    "search_conversations": ("read_project_conversation",),
}

DEFERRED_TOOLS: frozenset[str] = CODE_MODE_CORE_TOOLS - ALWAYS_LOADED_CODE_TOOLS

TOOL_SEARCH_ENV = "ORIN_CODE_TOOL_SEARCH"
_DISABLED_VALUES = frozenset({"off", "0", "false", "no"})


def tool_search_enabled(env: Mapping[str, str] = os.environ) -> bool:
    raw = str(env.get(TOOL_SEARCH_ENV, "") or "").strip().lower()
    return raw not in _DISABLED_VALUES
