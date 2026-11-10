from __future__ import annotations

import json
from pathlib import Path
import os
import sys
import time
from typing import Any

import httpx


def _emit(packet: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(packet, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _parse_argv(argv: list[str]) -> tuple[str | None, str]:
    mcp_config_path: str | None = None
    model = "claude-opus-5-5"
    idx = 0
    while idx < len(argv):
        if argv[idx] == "--mcp-config" and idx + 1 < len(argv):
            mcp_config_path = argv[idx + 1]
            idx += 2
        elif argv[idx] == "--model" and idx + 1 < len(argv):
            model = argv[idx + 1]
            idx += 2
        else:
            idx += 1
    return mcp_config_path, model


def _load_mcp_server(mcp_config_path: str | None) -> tuple[str, str, dict[str, str]]:
    if not mcp_config_path:
        return "orin", "", {}
    data = json.loads(Path(mcp_config_path).read_text(encoding="utf-8"))
    servers = data.get("mcpServers") or {}
    for name, cfg in servers.items():
        return str(name), str(cfg.get("url") or ""), dict(cfg.get("headers") or {})
    return "orin", "", {}


def _mcp_rpc(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    resp = httpx.post(url, json=payload, headers=headers, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def _extract_user_text(line_str: str) -> str:
    try:
        pkt = json.loads(line_str)
        content = pkt.get("message", {}).get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                str(b.get("text") or "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
    except Exception:
        pass
    return ""


def main() -> int:
    if "--version" in sys.argv:
        sys.stdout.write(f"{os.environ.get('FAKE_CLAUDE_VERSION', '2.1.309')} (Claude Code)\n")
        sys.stdout.flush()
        return 0

    mcp_config_path, model = _parse_argv(sys.argv[1:])
    server_name, mcp_url, mcp_headers = _load_mcp_server(mcp_config_path)
    mode = os.environ.get("FAKE_CLAUDE_MODE", "two_dependent_calls").strip()

    first_line = sys.stdin.readline()
    if not first_line:
        return 1
    first_user_text = _extract_user_text(first_line)

    if mode == "mcp_failed":
        _emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "sess-fake",
                "model": model,
                "apiKeySource": "none",
                "mcp_servers": [{"name": server_name, "status": "failed"}],
                "tools": [],
            }
        )
        return 0

    if mode == "api_key_leak":
        _emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "sess-fake",
                "model": model,
                "apiKeySource": "ANTHROPIC_API_KEY",
                "mcp_servers": [{"name": server_name, "status": "connected"}],
                "tools": [],
            }
        )
        return 0

    if mode == "no_init":
        _emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Bypassed init!"},
                },
            }
        )
        _emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Bypassed init!",
                "stop_reason": "end_turn",
                "num_turns": 1,
                "duration_ms": 5,
                "session_id": "sess-no-init",
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }
        )
        return 0

    if mode == "error_event_hang":
        _emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "sess-fake",
                "model": model,
                "apiKeySource": "none",
                "mcp_servers": [{"name": server_name, "status": "connected"}],
                "tools": [],
            }
        )
        _emit(
            {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "Overloaded stream error"},
            }
        )
        time.sleep(30.0)
        return 0

    tools_names: list[str] = []
    if mcp_url:
        _mcp_rpc(
            mcp_url,
            mcp_headers,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
        )
        listed = _mcp_rpc(
            mcp_url,
            mcp_headers,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        for t in listed.get("result", {}).get("tools", []):
            tools_names.append(f"mcp__{server_name}__{t['name']}")

    _emit(
        {
            "type": "system",
            "subtype": "init",
            "session_id": "sess-fake",
            "model": model,
            "apiKeySource": "none",
            "mcp_servers": [{"name": server_name, "status": "connected"}],
            "tools": tools_names,
        }
    )

    if mode == "silent_hang":
        sys.stdout.write("NON_JSON_BEFORE_IDLE\n")
        sys.stdout.flush()
        time.sleep(30.0)
        return 0

    if mode == "stdout_eof":
        sys.stdout.write("NON_JSON_STDOUT_LINE\n")
        sys.stdout.flush()
        sys.stderr.write("STDERR_DIAG_LINE\n")
        sys.stderr.flush()
        _emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "partial before eof"},
                },
            }
        )
        return 17

    if mode == "error_result":
        sys.stdout.write("NON_JSON_ERROR_CONTEXT\n")
        sys.stdout.flush()
        _emit(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "Execution failed in fake CLI",
                "stop_reason": "error",
                "num_turns": 1,
                "duration_ms": 12,
                "session_id": "sess-fake",
                "usage": {"input_tokens": 8, "output_tokens": 2},
            }
        )
        return 0

    if mode == "hold_until_eof":
        for _ in sys.stdin:
            pass
        return 0

    if mode == "persistent_followup":
        turn_idx = 1
        current_text = first_user_text
        while True:
            reply = f"Reply#{turn_idx}:{current_text}|timeout={os.environ.get('MCP_TOOL_TIMEOUT', '')}"
            _emit(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "message_start",
                        "message": {
                            "id": f"msg_p_{turn_idx}",
                            "usage": {
                                "input_tokens": 10 * turn_idx,
                                "output_tokens": 5,
                                "cache_read_input_tokens": 20,
                                "cache_creation_input_tokens": 5,
                            },
                        },
                    },
                }
            )
            _emit(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": reply},
                    },
                }
            )
            _emit(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": reply,
                    "stop_reason": "end_turn",
                    "num_turns": 1,
                    "duration_ms": 15,
                    "session_id": "sess-fake",
                    "usage": {
                        "input_tokens": 10 * turn_idx,
                        "output_tokens": 5,
                        "cache_read_input_tokens": 20,
                        "cache_creation_input_tokens": 5,
                    },
                }
            )
            next_line = sys.stdin.readline()
            if not next_line:
                break
            turn_idx += 1
            current_text = _extract_user_text(next_line)
        return 0

    if mode == "single_tool_call":
        tool_name = os.environ.get("FAKE_CLAUDE_TOOL_NAME", "echo")
        tool_args_json = os.environ.get("FAKE_CLAUDE_TOOL_ARGS", '{"text": "hello"}')
        tool_args = json.loads(tool_args_json)
        _emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "usage": {
                            "input_tokens": 12,
                            "output_tokens": 4,
                            "cache_read_input_tokens": 100,
                            "cache_creation_input_tokens": 10,
                        },
                    },
                },
            }
        )
        _emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": ""},
                },
            }
        )
        _emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tu_single_1",
                        "name": f"mcp__{server_name}__{tool_name}",
                        "input": {},
                    },
                },
            }
        )
        _emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": tool_args_json},
                },
            }
        )
        _emit(
            {
                "type": "stream_event",
                "event": {"type": "content_block_stop", "index": 1},
            }
        )
        call_res = _mcp_rpc(
            mcp_url,
            mcp_headers,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": tool_args,
                    "_meta": {"claudecode/toolUseId": "tu_single_1"},
                },
            },
        )
        out_text = call_res.get("result", {}).get("content", [{}])[0].get("text", "")
        final_text = f"Result={out_text}|timeout={os.environ.get('MCP_TOOL_TIMEOUT', '')}"
        _emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 2,
                    "delta": {"type": "text_delta", "text": final_text},
                },
            }
        )
        _emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": final_text,
                "stop_reason": "end_turn",
                "num_turns": 2,
                "duration_ms": 20,
                "session_id": "sess-fake",
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 8,
                    "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 10,
                },
            }
        )
        return 0

    # Default mode: "two_dependent_calls"
    # Step 1: call echo("A")
    _emit(
        {
            "type": "stream_event",
            "event": {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_read_input_tokens": 50,
                        "cache_creation_input_tokens": 8,
                    },
                },
            },
        }
    )
    _emit(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": ""},
            },
        }
    )
    _emit(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": f"mcp__{server_name}__echo",
                    "input": {},
                },
            },
        }
    )
    arg1_json = '{"text": "A"}'
    _emit(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": arg1_json},
            },
        }
    )
    _emit({"type": "stream_event", "event": {"type": "content_block_stop", "index": 1}})
    _emit(
        {
            "type": "assistant",
            "message": {
                "id": "msg_1",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": f"mcp__{server_name}__echo",
                        "input": {"text": "A"},
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 50,
                    "cache_creation_input_tokens": 8,
                },
            },
        }
    )

    res1 = _mcp_rpc(
        mcp_url,
        mcp_headers,
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "echo",
                "arguments": {"text": "A"},
                "_meta": {"claudecode/toolUseId": "tu_1"},
            },
        },
    )
    out1 = res1.get("result", {}).get("content", [{}])[0].get("text", "")

    # Step 2: call echo(out1 + "B")
    arg2_obj = {"text": f"{out1}B"}
    arg2_json = json.dumps(arg2_obj)
    _emit(
        {
            "type": "stream_event",
            "event": {
                "type": "message_start",
                "message": {
                    "id": "msg_2",
                    "usage": {
                        "input_tokens": 15,
                        "output_tokens": 6,
                        "cache_read_input_tokens": 60,
                        "cache_creation_input_tokens": 4,
                    },
                },
            },
        }
    )
    _emit(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "tu_2",
                    "name": f"mcp__{server_name}__echo",
                    "input": {},
                },
            },
        }
    )
    _emit(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": arg2_json},
            },
        }
    )
    _emit({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}})
    res2 = _mcp_rpc(
        mcp_url,
        mcp_headers,
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "echo",
                "arguments": arg2_obj,
                "_meta": {"claudecode/toolUseId": "tu_2"},
            },
        },
    )
    out2 = res2.get("result", {}).get("content", [{}])[0].get("text", "")

    final_reply = f"Final:{out2}"
    _emit(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": final_reply},
            },
        }
    )
    _emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": final_reply,
            "stop_reason": "end_turn",
            "num_turns": 3,
            "duration_ms": 42,
            "session_id": "sess-fake",
            "usage": {
                "input_tokens": 30,
                "output_tokens": 15,
                "cache_read_input_tokens": 110,
                "cache_creation_input_tokens": 12,
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
