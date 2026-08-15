"""A minimal MCP server over stdio, used only by the MCP integration test.

Speaks just enough JSON-RPC to satisfy McpClient: initialize, tools/list (one
tool, "echo"), and tools/call. Not a stand-in for a production MCP server.
"""
from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2025-06-18"


def _write(frame: dict) -> None:
    sys.stdout.write(json.dumps(frame) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        frame = json.loads(line)
        method = frame.get("method")
        request_id = frame.get("id")

        if method == "notifications/initialized":
            continue  # a notification carries no id and expects no response

        if method == "initialize":
            _write({
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "serverInfo": {"name": "echo-fixture"},
                    "capabilities": {"tools": {}},
                },
            })
        elif method == "tools/list":
            _write({
                "jsonrpc": "2.0", "id": request_id,
                "result": {"tools": [{
                    "name": "echo",
                    "description": "Echoes the given text back.",
                    "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                }]},
            })
        elif method == "tools/call":
            params = frame.get("params") or {}
            arguments = params.get("arguments") or {}
            _write({
                "jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text", "text": str(arguments.get("text", ""))}], "isError": False},
            })
        elif request_id is not None:
            _write({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"unknown method {method}"}})


if __name__ == "__main__":
    main()
