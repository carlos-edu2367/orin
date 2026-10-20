"""``orin code mcp serve``: Orin's file and shell tools as an MCP server on stdio, for another MCP client.

The tools run in this process, on the current folder, in ``dontAsk`` mode: there is nobody to ask, so whatever would
need a confirmation is refused, and writing or running commands needs an allow rule in the person's own settings
(read with the folder untrusted: a repository cannot grant itself anything). Nothing reaches outside the folder.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

from agentos.agentic.tools.public_names import public_name

PROTOCOL_VERSION = "2025-06-18"
# Claude Code's names for the tools offered, as Orin's internal names.
SERVED_TOOLS = ("read_file", "glob_files", "search_files", "list_files", "write_file", "edit_file", "run_command")


def _toolset(cwd: Path):
    from agentos.agentic.agent_tools import AgentToolset
    from agentos.agentic.workspace import ConversationWorkspace

    return AgentToolset(ConversationWorkspace.at_root(cwd), public_tool_names=True, code_mode_active=True,
                        permission_mode="dont_ask", non_interactive_ask="deny")


def _tools(toolset) -> list[dict[str, Any]]:
    tools = []
    for schema in toolset.schemas(set(SERVED_TOOLS)):
        function = schema.get("function") if isinstance(schema.get("function"), dict) else schema
        tools.append({"name": public_name(str(function.get("name") or "")), "description": str(function.get("description") or "")[:2000],
                      "inputSchema": function.get("parameters") or {"type": "object"}})
    return tools


def handle(toolset, frame: dict[str, Any]) -> dict[str, Any] | None:
    method = frame.get("method")
    request_id = frame.get("id")
    if request_id is None:
        return None  # notifications/initialized and friends
    if method == "initialize":
        result: dict[str, Any] = {"protocolVersion": PROTOCOL_VERSION, "serverInfo": {"name": "orin-code"},
                                  "capabilities": {"tools": {}}}
    elif method == "tools/list":
        result = {"tools": _tools(toolset)}
    elif method == "tools/call":
        params = frame.get("params") if isinstance(frame.get("params"), dict) else {}
        name = str(params.get("name") or "")
        allowed = {tool["name"] for tool in _tools(toolset)}
        if name not in allowed:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        outcome = toolset.invoke(name, dict(params.get("arguments") or {}))
        result = {"content": [{"type": "text", "text": str(outcome.content or outcome.summary or "")}],
                  "isError": outcome.status != "succeeded"}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(*, cwd: Path, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    toolset = _toolset(cwd)
    for line in stdin:
        if not line.strip():
            continue
        try:
            frame = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        if not isinstance(frame, dict):
            continue
        answer = handle(toolset, frame)
        if answer is not None:
            stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


__all__ = ["SERVED_TOOLS", "handle", "serve"]
