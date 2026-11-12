"""Fake `agy` CLI script for E2E native-loop tests without consuming real credits."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import Any
import urllib.request

_BUILTIN_TOOLS: list[str] = [
    "ask_custom_permission",
    "ask_permission",
    "ask_question",
    "browser_click_element",
    "browser_drag_pixel_to_pixel",
    "browser_get_dom",
    "browser_get_network_request",
    "browser_input",
    "browser_list_network_requests",
    "browser_mouse_down",
    "browser_mouse_up",
    "browser_move_mouse",
    "browser_press_key",
    "browser_refresh_page",
    "browser_resize_window",
    "browser_scroll",
    "browser_scroll_dom",
    "browser_select_option",
    "browser_subagent",
    "call_mcp_tool",
    "capture_browser_console_logs",
    "capture_browser_screenshot",
    "click_browser_pixel",
    "command_status",
    "define_subagent",
    "delete_knowledge",
    "execute_browser_javascript",
    "find_by_name",
    "finish",
    "generate_image",
    "grep_search",
    "invoke_subagent",
    "list_browser_pages",
    "list_dir",
    "list_permissions",
    "list_plugin_accounts",
    "list_resources",
    "manage_inbox",
    "manage_subagents",
    "manage_task",
    "multi_replace_file_content",
    "notebook_edit",
    "notebook_execution",
    "open_browser_url",
    "read_browser_page",
    "read_resource",
    "read_url_content",
    "replace_file_content",
    "run_command",
    "run_workflow",
    "schedule",
    "search_marketplace",
    "search_web",
    "sed_file",
    "send_command_input",
    "send_message",
    "view_file",
    "wait",
    "wait_5_seconds",
    "write_to_file",
]

_MODELS_OUTPUT = """\
gemini-4-argon-medium\tGemini 4 Argon (Medium)
gemini-4-argon-high\tGemini 4 Argon (High)
gemini-4-argon-low\tGemini 4 Argon (Low)
gemini-3.8-flash-high\tGemini 3.8 Flash (High)
gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)
gemini-3.8-flash-low\tGemini 3.8 Flash (Low)
claude-opus-5-5-low\tClaude Opus 5.5 (Low)
claude-opus-5-5-medium\tClaude Opus 5.5 (Medium)
claude-opus-5-5-high\tClaude Opus 5.5 (High)
claude-sonnet-5-5-low\tClaude Sonnet 5.5 (Low)
claude-sonnet-5-5-medium\tClaude Sonnet 5.5 (Medium)
claude-sonnet-5-5-high\tClaude Sonnet 5.5 (High)
gpt-oss-120b-medium\tGPT-OSS 120B (Medium)
"""


def _emit(packet: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(packet, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _mcp_rpc(
    url: str, headers: dict[str, str], payload: dict[str, Any]
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(
        url,
        data=data,
        headers=req_headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_argv(
    argv: list[str],
) -> tuple[str, str | None, str | None, str | None]:
    model = "gemini-4-argon-medium"
    conversation_id: str | None = None
    agent_name: str | None = None
    settings_path: str | None = None

    idx = 0
    while idx < len(argv):
        token = argv[idx]
        if token == "-p":
            if idx + 1 >= len(argv) or argv[idx + 1].startswith("-"):
                raise ValueError(
                    'Error: -p took "--input-format" as its prompt'
                )
            idx += 2
        elif token in ("--model", "-m") and idx + 1 < len(argv):
            model = argv[idx + 1]
            idx += 2
        elif token == "--conversation" and idx + 1 < len(argv):
            conversation_id = argv[idx + 1]
            idx += 2
        elif token == "--agent" and idx + 1 < len(argv):
            agent_name = argv[idx + 1]
            idx += 2
        elif token == "--settings" and idx + 1 < len(argv):
            settings_path = argv[idx + 1]
            idx += 2
        else:
            idx += 1

    return model, conversation_id, agent_name, settings_path


def _extract_user_text(raw_line: str) -> str:
    pkt = json.loads(raw_line)
    if pkt.get("event") != "user":
        raise ValueError(f"Expected event='user', got {pkt.get('event')!r}")
    msg = pkt.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        raise ValueError("Expected message.content list")
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            raise ValueError(f"Unsupported block type: {block!r}")
        parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv or "-v" in argv:
        sys.stdout.write(f"{os.environ.get('FAKE_AGY_VERSION', '1.5.2')}\n")
        sys.stdout.flush()
        return 0

    if argv and argv[0] == "models":
        sys.stdout.write(_MODELS_OUTPUT)
        sys.stdout.flush()
        return 0

    try:
        model, resume_conv_id, agent_name, settings_path = _parse_argv(argv)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        sys.stderr.flush()
        return 2

    cwd = Path.cwd()
    mcp_cfg_file = cwd / ".agents" / "mcp_config.json"
    agent_md_file = cwd / ".agents" / "agents" / f"{agent_name or 'orin'}.md"

    def _read_mcp_config() -> tuple[str, dict[str, str], str]:
        url, headers, name = "", {}, "orin"
        if mcp_cfg_file.is_file():
            cfg_data = json.loads(mcp_cfg_file.read_text(encoding="utf-8"))
            servers = cfg_data.get("mcpServers") or {}
            for s_name, s_cfg in servers.items():
                name = str(s_name)
                url = str(s_cfg.get("url") or "")
                headers = dict(s_cfg.get("headers") or {})
                break
        return url, headers, name

    mcp_url, mcp_headers, server_name = _read_mcp_config()

    if settings_path:
        settings_file = Path(settings_path)
        if not settings_file.is_file():
            sys.stderr.write(f"Missing settings file: {settings_path}\n")
            return 3

    sys_prompt_excerpt = ""
    if agent_md_file.is_file():
        md_text = agent_md_file.read_text(encoding="utf-8")
        if "# Instructions" in md_text:
            sys_prompt_excerpt = md_text.split("# Instructions", 1)[1].strip()

    conv_id = resume_conv_id or "14140a4a-4bab-4165-ab52-ee036a3ab56a"
    turn_idx = 0
    step_idx = 0
    cum_input = 0
    cum_output = 0
    cum_thinking = 0
    cum_cache_read = 0

    while True:
        raw_line = sys.stdin.readline()
        if not raw_line:
            break
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            prompt_text = _extract_user_text(stripped)
        except Exception as exc:
            sys.stderr.write(f"Invalid stdin packet: {exc}\n")
            return 1

        turn_idx += 1
        if turn_idx == 1:
            _emit(
                {
                    "event": "init",
                    "conversation_id": conv_id,
                    "init": {
                        "model": model,
                        "cwd": str(cwd),
                        "agent": agent_name or "orin",
                        "tools": _BUILTIN_TOOLS,
                        "permission_mode": "request-review",
                    },
                }
            )

        _emit(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": conv_id,
                    "step_index": step_idx,
                    "state": "DONE",
                    "step_type": "user_input",
                },
            }
        )
        step_idx += 1

        # agy's own read-only tool next to an Orin tool, as Gemini did (manage_task list + mcp_orin_Bash):
        # called again on a resumed turn only with BENIGN_BUILTIN_ALWAYS.
        if "BENIGN_BUILTIN" in prompt_text and (
            "BENIGN_BUILTIN_ALWAYS" in prompt_text or "does not exist here" not in prompt_text
        ):
            _emit(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": conv_id,
                        "step_index": step_idx,
                        "state": "ACTIVE",
                        "step_type": "tool",
                        "tool_name": "manage_task",
                        "tool_info": {"name": "manage_task", "parameters": {"Action": "list"}},
                    },
                }
            )
            step_idx += 1
            # Orin stops the process here; if it did not, this would be agy's own (wrong) answer.
            _emit(
                {
                    "event": "result",
                    "result": {
                        "conversation_id": conv_id,
                        "status": "SUCCESS",
                        "response": "No background tasks are currently running.",
                        "duration_seconds": 0.1,
                        "num_turns": turn_idx,
                        "usage": {"input_tokens": 10, "output_tokens": 1, "thinking_tokens": 0,
                                  "cache_read_tokens": 0, "total_tokens": 11},
                    },
                }
            )
            continue

        native_read = re.search(r"NATIVE_READ_(DENIED|DONE)=(\S+)", prompt_text)
        if native_read is not None:
            # A native read_file: `DENIED` is what agy emits when the PreToolUse hook refuses the call
            # (ACTIVE, then ERROR with the hook's message) and the model carries on; `DONE` is a read
            # that really completed.
            denied = native_read.group(1) == "DENIED"
            info: dict[str, Any] = {
                "name": "read_file",
                "parameters": {"Path": native_read.group(2)},
            }
            _emit(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": conv_id,
                        "step_index": step_idx,
                        "state": "ACTIVE",
                        "step_type": "tool",
                        "tool_name": "read_file",
                        "tool_info": info,
                    },
                }
            )
            if denied:
                info = {
                    **info,
                    "error": {
                        "type": "TOOL_ERROR",
                        "message": "tool call denied by pre-tool hook: Extension .txt is not allowed",
                    },
                }
            _emit(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": conv_id,
                        "step_index": step_idx,
                        "state": "ERROR" if denied else "DONE",
                        "step_type": "tool",
                        "tool_name": "read_file",
                        "tool_info": info,
                    },
                }
            )
            step_idx += 1
            _emit(
                {
                    "event": "result",
                    "result": {
                        "conversation_id": conv_id,
                        "status": "SUCCESS",
                        "response": "carried on after read_file",
                        "duration_seconds": 0.1,
                        "num_turns": turn_idx,
                        "usage": {"input_tokens": 10, "output_tokens": 1, "thinking_tokens": 0,
                                  "cache_read_tokens": 0, "total_tokens": 11},
                    },
                }
            )
            continue

        if "FORBIDDEN_BUILTIN" in prompt_text:
            _emit(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": conv_id,
                        "step_index": step_idx,
                        "state": "ACTIVE",
                        "step_type": "tool",
                        "tool_name": "run_command",
                        "tool_info": {
                            "name": "run_command",
                            "parameters": {"CommandLine": "whoami"},
                        },
                    },
                }
            )
            step_idx += 1
            _emit(
                {
                    "event": "result",
                    "result": {
                        "conversation_id": conv_id,
                        "status": "SUCCESS",
                        "response": "ran command",
                        "duration_seconds": 0.1,
                        "num_turns": turn_idx,
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 10,
                            "thinking_tokens": 0,
                            "cache_read_tokens": 0,
                            "total_tokens": 110,
                        },
                    },
                }
            )
            return 0

        if "CALL_MCP" in prompt_text and "REREAD_MCP_CONFIG" in prompt_text:
            # Read the folder's config again at call time: in a folder another process shares, a later
            # process's config has replaced this one's by now.
            mcp_url, mcp_headers, server_name = _read_mcp_config()
        if "CALL_MCP" in prompt_text and mcp_url:
            _mcp_rpc(
                mcp_url,
                mcp_headers,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26"},
                },
            )
            list_resp = _mcp_rpc(
                mcp_url,
                mcp_headers,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            tools = list_resp.get("result", {}).get("tools", [])
            t_name = str(tools[0]["name"]) if tools else "echo"

            _emit(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": conv_id,
                        "step_index": step_idx,
                        "state": "DONE",
                        "step_type": "agent_response",
                        "duration_seconds": 0.5,
                        "usage": {
                            "input_tokens": 684,
                            "output_tokens": 62,
                            "thinking_tokens": 31,
                            "cache_read_tokens": 100,
                            "total_tokens": 746,
                        },
                    },
                }
            )
            step_idx += 1

            mcp_tool_name = f"mcp_{server_name}_{t_name}"
            _emit(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": conv_id,
                        "step_index": step_idx,
                        "state": "ACTIVE",
                        "step_type": "tool",
                        "tool_name": mcp_tool_name,
                        "tool_info": {
                            "name": mcp_tool_name,
                            "parameters": {"text": "hello-from-agy"},
                        },
                    },
                }
            )
            call_resp = _mcp_rpc(
                mcp_url,
                mcp_headers,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": t_name,
                        "arguments": {"text": "hello-from-agy"},
                    },
                },
            )
            content_list = call_resp.get("result", {}).get("content", [])
            tool_out = (
                str(content_list[0].get("text") or "")
                if content_list and isinstance(content_list[0], dict)
                else ""
            )
            _emit(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": conv_id,
                        "step_index": step_idx,
                        "state": "DONE",
                        "step_type": "tool",
                        "tool_name": mcp_tool_name,
                        "duration_seconds": 0.04,
                        "tool_info": {
                            "name": mcp_tool_name,
                            "parameters": {"text": "hello-from-agy"},
                            "output": tool_out,
                        },
                    },
                }
            )
            step_idx += 1

            _emit(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": conv_id,
                        "step_index": step_idx,
                        "state": "ACTIVE",
                        "step_type": "agent_response",
                        "text_delta": tool_out,
                    },
                }
            )
            _emit(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": conv_id,
                        "step_index": step_idx,
                        "state": "DONE",
                        "step_type": "agent_response",
                        "text_delta": "\n",
                        "duration_seconds": 0.4,
                        "usage": {
                            "input_tokens": 835,
                            "output_tokens": 13,
                            "thinking_tokens": 0,
                            "cache_read_tokens": 200,
                            "total_tokens": 848,
                        },
                    },
                }
            )
            step_idx += 1
            cum_input += 684 + 835
            cum_output += 62 + 13
            cum_thinking += 31
            cum_cache_read += 100 + 200
            _emit(
                {
                    "event": "result",
                    "result": {
                        "conversation_id": conv_id,
                        "status": "SUCCESS",
                        "response": f"{tool_out}\n",
                        "duration_seconds": 1.0,
                        "num_turns": turn_idx,
                        "usage": {
                            "input_tokens": cum_input,
                            "output_tokens": cum_output,
                            "thinking_tokens": cum_thinking,
                            "cache_read_tokens": cum_cache_read,
                            "total_tokens": cum_input + cum_output,
                        },
                    },
                }
            )
            continue

        # Standard text turn (supports Turn 1 and Turn 2+ over persistent stdin)
        if turn_idx == 1:
            part1, part2 = "PONG_AG", "Y"
            step_in, step_out = 543, 5
        else:
            part1, part2 = "PONG_AG", f"Y_T{turn_idx}"
            step_in, step_out = 629, 8
        if sys_prompt_excerpt and "CUSTOM_TOKEN:" in sys_prompt_excerpt:
            tok = sys_prompt_excerpt.split("CUSTOM_TOKEN:", 1)[1].split()[0]
            part2 = f"{part2}:{tok}"

        _emit(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": conv_id,
                    "step_index": step_idx,
                    "state": "ACTIVE",
                    "step_type": "agent_response",
                    "text_delta": part1,
                },
            }
        )
        _emit(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": conv_id,
                    "step_index": step_idx,
                    "state": "ACTIVE",
                    "step_type": "agent_response",
                    "text_delta": part2,
                },
            }
        )
        _emit(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": conv_id,
                    "step_index": step_idx,
                    "state": "DONE",
                    "step_type": "agent_response",
                    "text_delta": "\n",
                    "duration_seconds": 1.2,
                    "usage": {
                        "input_tokens": step_in,
                        "output_tokens": step_out,
                        "thinking_tokens": 0,
                        "cache_read_tokens": 0,
                        "total_tokens": step_in + step_out,
                    },
                },
            }
        )
        step_idx += 1
        cum_input += step_in
        cum_output += step_out
        full_resp = f"{part1}{part2}\n"
        _emit(
            {
                "event": "result",
                "result": {
                    "conversation_id": conv_id,
                    "status": "SUCCESS",
                    "response": full_resp,
                    "duration_seconds": 1.3,
                    "num_turns": turn_idx,
                    "usage": {
                        "input_tokens": cum_input,
                        "output_tokens": cum_output,
                        "thinking_tokens": cum_thinking,
                        "cache_read_tokens": cum_cache_read,
                        "total_tokens": cum_input + cum_output,
                    },
                },
            }
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
