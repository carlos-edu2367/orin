"""Fake `codex` CLI script for E2E native-loop tests without consuming real credits."""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any
import urllib.request

_MCP_URL_RE = re.compile(r"\Amcp_servers\.([A-Za-z0-9_-]+)\.url=(.+)\Z")
_MCP_BEARER_ENV_RE = re.compile(
    r"\Amcp_servers\.([A-Za-z0-9_-]+)\.bearer_token_env_var=(.+)\Z"
)


def _emit(packet: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(packet, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _unquote_toml_str(raw: str) -> str:
    text = raw.strip()
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        try:
            val = json.loads(text)
            if isinstance(val, str):
                return val
        except Exception:
            return text[1:-1]
    return text


def _parse_argv(argv: list[str]) -> tuple[bool, str | None, str, str, str]:
    is_resume = False
    resume_thread_id: str | None = None
    if len(argv) >= 3 and argv[0] == "exec" and argv[1] == "resume":
        is_resume = True
        resume_thread_id = argv[2]

    server_name = "orin"
    mcp_url = ""
    bearer_env_var = "ORIN_MCP_TOKEN"

    idx = 0
    while idx < len(argv):
        token = argv[idx]
        if token in ("-c", "--config") and idx + 1 < len(argv):
            c_val = argv[idx + 1]
            m_url = _MCP_URL_RE.match(c_val)
            if m_url:
                server_name = m_url.group(1)
                mcp_url = _unquote_toml_str(m_url.group(2))
            m_env = _MCP_BEARER_ENV_RE.match(c_val)
            if m_env:
                server_name = m_env.group(1)
                bearer_env_var = _unquote_toml_str(m_env.group(2))
            idx += 2
        else:
            idx += 1

    return is_resume, resume_thread_id, server_name, mcp_url, bearer_env_var


def _mcp_rpc(
    url: str, bearer_token: str, payload: dict[str, Any]
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer_token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv or "-V" in argv:
        sys.stdout.write("codex-cli 0.167.0\n")
        sys.stdout.flush()
        return 0

    is_resume, resume_thread_id, server_name, mcp_url, bearer_env_var = (
        _parse_argv(argv)
    )

    raw_stdin = sys.stdin.read()
    prompt_text = raw_stdin.strip()
    if not prompt_text:
        return 2

    thread_id = (
        resume_thread_id
        if is_resume and resume_thread_id
        else "thread-e2e-1"
    )

    if (
        "FORBIDDEN_SHELL" in prompt_text
        or os.environ.get("FAKE_CODEX_MODE") == "forbidden_shell"
    ):
        _emit({"type": "thread.started", "thread_id": thread_id})
        _emit({"type": "turn.started"})
        _emit(
            {
                "type": "item.started",
                "item": {
                    "id": "item_0",
                    "type": "command_execution",
                    "command": "\"C:\\\\WINDOWS\\\\System32\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe\" -Command 'echo FORBIDDEN_SHELL_TEST'",
                    "aggregated_output": "",
                    "exit_code": None,
                    "status": "in_progress",
                },
            }
        )
        _emit(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "command_execution",
                    "command": "\"C:\\\\WINDOWS\\\\System32\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe\" -Command 'echo FORBIDDEN_SHELL_TEST'",
                    "aggregated_output": "FORBIDDEN_SHELL_TEST\r\n",
                    "exit_code": 0,
                    "status": "completed",
                },
            }
        )
        _emit(
            {
                "type": "item.completed",
                "item": {"id": "item_1", "type": "agent_message", "text": "OK"},
            }
        )
        _emit(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 16742,
                    "cached_input_tokens": 8192,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 65,
                    "reasoning_output_tokens": 0,
                },
            }
        )
        return 0

    if not mcp_url:
        sys.stderr.write("Missing mcp_servers.<name>.url in -c flags\n")
        return 3

    bearer_token = os.environ.get(bearer_env_var, "")
    if not bearer_token:
        sys.stderr.write(f"Missing bearer token in env var {bearer_env_var}\n")
        return 4

    _mcp_rpc(
        mcp_url,
        bearer_token,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
    )
    list_resp = _mcp_rpc(
        mcp_url,
        bearer_token,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    tools = list_resp.get("result", {}).get("tools", [])
    tool_name = str(tools[0]["name"]) if tools else "echo"

    call_arg_text = (
        f"mode={'resume' if is_resume else 'new'}"
        f"|thread={thread_id}"
        f"|prompt={prompt_text}"
    )
    call_resp = _mcp_rpc(
        mcp_url,
        bearer_token,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": {"text": call_arg_text},
            },
        },
    )
    content_list = call_resp.get("result", {}).get("content", [])
    tool_out = (
        str(content_list[0].get("text") or "")
        if content_list and isinstance(content_list[0], dict)
        else ""
    )

    _emit({"type": "thread.started", "thread_id": thread_id})
    _emit({"type": "turn.started"})
    _emit(
        {
            "type": "item.started",
            "item": {
                "id": "item_0",
                "type": "mcp_tool_call",
                "server": server_name,
                "tool": tool_name,
                "arguments": {"text": prompt_text},
                "result": None,
                "error": None,
                "status": "in_progress",
            },
        }
    )
    _emit(
        {
            "type": "item.completed",
            "item": {
                "id": "item_0",
                "type": "mcp_tool_call",
                "server": server_name,
                "tool": tool_name,
                "arguments": {"text": prompt_text},
                "result": {
                    "content": [{"type": "text", "text": tool_out}],
                    "structured_content": None,
                },
                "error": None,
                "status": "completed",
            },
        }
    )
    final_msg = f"Codex[{thread_id}]:{tool_out}"
    _emit(
        {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "agent_message",
                "text": final_msg,
            },
        }
    )
    _emit(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 24601,
                "cached_input_tokens": 16000,
                "cache_write_input_tokens": 0,
                "output_tokens": 90,
                "reasoning_output_tokens": 0,
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
