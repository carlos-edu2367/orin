"""System prompt guidance sections for Orin Chat and Agentic Runtime."""
from __future__ import annotations

from collections.abc import Sequence

EXTENSION_TOOLS_GUIDANCE = (
    "## Extensions (MCP Servers, Plugins, and Skills)\n"
    "- When the user asks to connect or install an MCP server, call `list_mcp_catalog` first to find the server entry and required credentials, then call `configure_mcp` (passing `secret_names` only, never credential values) to present the approval card and wait for the user.\n"
    "- When the user asks to find or install a plugin, call `search_plugin` and `install_plugin(reference)` (or `inspect_plugin` to preview without installing) to present the plugin approval card and wait for the user.\n"
    "- When the user asks to create or update a reusable procedural Skill, search existing Skills with `search_skills` / `list_skills` first, then call `create_skill` or `edit_skill` with explicit `Workflow` and `Validation` sections."
)

PROPOSE_CODE_SESSION_GUIDANCE = (
    "## Orin Code Handoff (`propose_code_session`)\n"
    "- When the user asks for multi-file code implementation, refactoring, running test suites, or explicitly asks to open or continue work in Orin Code, call `propose_code_session(title=..., prompt=..., workspace_path=..., provider=..., model_id=..., permission_mode=...)`.\n"
    "- Write a self-contained `prompt` summarizing the objective, context discussed in this chat, target files, and acceptance criteria so the new Orin Code session can start immediately.\n"
    "- After calling `propose_code_session`, stop and wait for the user's decision on the interactive card."
)

PROJECT_TOOLS_GUIDANCE = (
    "## Projects and Cross-Session Continuity\n"
    "- When the user asks to list their projects, call `list_projects`.\n"
    "- When the user asks to turn the current session/workspace into a new project, call `create_project_from_session(name=..., description=..., workspace_path=...)`.\n"
    "- When the user asks to move or attach this session into an existing project, call `move_session_to_project(project_id=...)` (calling `list_projects` first if you need to look up the `project_id`).\n"
    "- When the user asks to continue work from or inspect another chat or Orin Code session in the project, call `read_project_conversation(conversation_id=..., limit=...)`."
)


def extension_guidance_lines(tool_names: Sequence[str]) -> list[str]:
    names = set(tool_names)
    out: list[str] = []
    if {"list_mcp_catalog", "configure_mcp", "search_plugin", "install_plugin"} & names:
        out.extend(["", EXTENSION_TOOLS_GUIDANCE])
    if "propose_code_session" in names:
        out.extend(["", PROPOSE_CODE_SESSION_GUIDANCE])
    if {"list_projects", "create_project_from_session", "move_session_to_project", "read_project_conversation"} & names:
        out.extend(["", PROJECT_TOOLS_GUIDANCE])
    return out


SYSTEM_PROMPT = "\n\n".join((
    EXTENSION_TOOLS_GUIDANCE,
    PROPOSE_CODE_SESSION_GUIDANCE,
    PROJECT_TOOLS_GUIDANCE,
))

__all__ = [
    "EXTENSION_TOOLS_GUIDANCE",
    "PROJECT_TOOLS_GUIDANCE",
    "PROPOSE_CODE_SESSION_GUIDANCE",
    "SYSTEM_PROMPT",
    "extension_guidance_lines",
]
