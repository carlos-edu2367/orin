from __future__ import annotations

import json
import httpx
import pytest

from agentos.providers.cli_tools_mcp.server import TurnToolServer
from agentos.providers.cli_tools_mcp.types import (
    McpToolSpec,
    ToolCallRequest,
    ToolCallResult,
)


class _GrowingExecutor:
    def __init__(self) -> None:
        self.extra = False

    def list_tools(self) -> list[McpToolSpec]:
        base = [
            McpToolSpec(
                name="ToolSearch",
                description="s",
                input_schema={"type": "object"},
                read_only=True,
            )
        ]
        if self.extra:
            base.append(
                McpToolSpec(
                    name="NotebookEdit",
                    description="n",
                    input_schema={"type": "object"},
                )
            )
        return base

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult:
        if request.name == "ToolSearch":
            self.extra = True
            return ToolCallResult(text="loaded", tools_changed=True)
        return ToolCallResult(text="ok", tools_changed=False)


@pytest.mark.anyio
async def test_server_emits_list_changed_sse_when_tools_changed() -> None:
    srv = TurnToolServer(_GrowingExecutor())
    handle = await srv.start()
    try:
        headers = {
            "Authorization": f"Bearer {handle.token}",
            "Accept": "application/json, text/event-stream",
        }
        async with httpx.AsyncClient() as client:
            r_init = await client.post(
                handle.url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26"},
                },
            )
            assert (
                r_init.json()["result"]["capabilities"]["tools"]["listChanged"]
                is True
            )

            r1 = await client.post(
                handle.url,
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert [t["name"] for t in r1.json()["result"]["tools"]] == [
                "ToolSearch"
            ]

            r2 = await client.post(
                handle.url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "ToolSearch", "arguments": {}},
                },
            )
            assert r2.headers["content-type"].startswith("text/event-stream")
            assert "notifications/tools/list_changed" in r2.text
            data_lines = [
                json.loads(line[len("data: ") :])
                for line in r2.text.splitlines()
                if line.startswith("data: ")
            ]
            assert len(data_lines) == 2
            assert data_lines[0] == {
                "jsonrpc": "2.0",
                "method": "notifications/tools/list_changed",
            }
            assert data_lines[1]["id"] == 2
            assert data_lines[1]["result"]["isError"] is False

            r3 = await client.post(
                handle.url,
                headers=headers,
                json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            )
            assert [t["name"] for t in r3.json()["result"]["tools"]] == [
                "ToolSearch",
                "NotebookEdit",
            ]

            # Regular call without tools_changed returns application/json
            r4 = await client.post(
                handle.url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "NotebookEdit", "arguments": {}},
                },
            )
            assert r4.headers["content-type"].startswith("application/json")
            assert r4.json()["result"]["content"][0]["text"] == "ok"
    finally:
        await handle.close()
