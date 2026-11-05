from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any, Mapping, Sequence

import httpx
import pytest

from agentos.providers.cli_tools_mcp import server as server_mod
from agentos.providers.cli_tools_mcp.server import (
    BackgroundTurnToolServer,
    TurnToolServer,
)
from agentos.providers.cli_tools_mcp.types import (
    McpToolSpec,
    ToolCallRequest,
    ToolCallResult,
)


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[ToolCallRequest] = []
        self.gate: threading.Event | None = None
        self.entered = threading.Event()
        self.raise_exc: Exception | None = None
        self.image_data: str | None = None

    def list_tools(self) -> Sequence[McpToolSpec]:
        return [
            McpToolSpec(
                name="Read",
                description="Read a file from disk",
                input_schema={
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}},
                    "required": ["file_path"],
                },
                read_only=True,
            ),
            McpToolSpec(
                name="Write",
                description="Write a file to disk",
                input_schema={
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}},
                    "required": ["file_path"],
                },
                read_only=False,
            ),
        ]

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult:
        self.calls.append(request)
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(timeout=5.0)
        if self.raise_exc is not None:
            raise self.raise_exc
        images: tuple[Mapping[str, str], ...] = ()
        if self.image_data is not None:
            images = ({"data": self.image_data, "mime_type": "image/png"},)
        return ToolCallResult(
            text=f"read:{request.arguments.get('file_path')}",
            is_error=False,
            images=images,
        )


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_initialize_list_and_call_round_trip() -> None:
    fake = _FakeExecutor()
    bg = BackgroundTurnToolServer.start(TurnToolServer(fake))
    try:
        url = bg.handle.url
        headers = _auth_headers(bg.handle.token)

        init_resp = httpx.post(
            url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            headers=headers,
            timeout=2.0,
        )
        assert init_resp.status_code == 200
        assert "mcp-session-id" in {k.lower() for k in init_resp.headers.keys()}
        assert init_resp.json()["result"]["protocolVersion"] == "2025-06-18"

        notif_resp = httpx.post(
            url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
            timeout=2.0,
        )
        assert notif_resp.status_code == 202

        list_resp = httpx.post(
            url,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=headers,
            timeout=2.0,
        )
        tools = list_resp.json()["result"]["tools"]
        by_name = {t["name"]: t for t in tools}
        assert "Read" in by_name and "Write" in by_name
        assert by_name["Read"]["inputSchema"]["required"] == ["file_path"]
        assert by_name["Read"]["annotations"]["readOnlyHint"] is True
        assert by_name["Write"]["annotations"]["readOnlyHint"] is False

        call_resp = httpx.post(
            url,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "Read",
                    "arguments": {"file_path": "a.py"},
                    "_meta": {"claudecode/toolUseId": "toolu_123"},
                },
            },
            headers=headers,
            timeout=2.0,
        )
        res = call_resp.json()["result"]
        assert res["isError"] is False
        assert res["content"] == [{"type": "text", "text": "read:a.py"}]
        assert len(fake.calls) == 1
        assert fake.calls[0].meta["claudecode/toolUseId"] == "toolu_123"
    finally:
        bg.close()


def test_rejects_missing_or_wrong_token() -> None:
    fake = _FakeExecutor()
    bg = BackgroundTurnToolServer.start(TurnToolServer(fake))
    try:
        url = bg.handle.url
        r1 = httpx.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            timeout=2.0,
        )
        assert r1.status_code == 401

        r2 = httpx.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": "Bearer wrong-token"},
            timeout=2.0,
        )
        assert r2.status_code == 401
    finally:
        bg.close()


def test_rejects_foreign_origin_and_host() -> None:
    fake = _FakeExecutor()
    bg = BackgroundTurnToolServer.start(TurnToolServer(fake))
    try:
        url = bg.handle.url
        headers = _auth_headers(bg.handle.token)

        r_origin = httpx.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**headers, "Origin": "https://evil.example.com"},
            timeout=2.0,
        )
        assert r_origin.status_code == 403

        r_host = httpx.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**headers, "Host": "evil.example.com:80"},
            timeout=2.0,
        )
        assert r_host.status_code == 421
    finally:
        bg.close()


def test_rejects_non_loopback_bind() -> None:
    fake = _FakeExecutor()
    with pytest.raises(ValueError):
        TurnToolServer(fake, host="0.0.0.0")


def test_image_result_becomes_mcp_image_block() -> None:
    fake = _FakeExecutor()
    fake.image_data = "iVBOR"
    bg = BackgroundTurnToolServer.start(TurnToolServer(fake))
    try:
        resp = httpx.post(
            bg.handle.url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "Read", "arguments": {"file_path": "img.png"}},
            },
            headers=_auth_headers(bg.handle.token),
            timeout=2.0,
        )
        content = resp.json()["result"]["content"]
        assert content[1] == {
            "type": "image",
            "data": "iVBOR",
            "mimeType": "image/png",
        }
    finally:
        bg.close()


def test_executor_exception_is_tool_error_not_jsonrpc_error() -> None:
    fake = _FakeExecutor()
    fake.raise_exc = RuntimeError("disk boom")
    bg = BackgroundTurnToolServer.start(TurnToolServer(fake))
    try:
        resp = httpx.post(
            bg.handle.url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "Read", "arguments": {"file_path": "a.py"}},
            },
            headers=_auth_headers(bg.handle.token),
            timeout=2.0,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" not in body
        assert body["result"]["isError"] is True
        assert "RuntimeError: disk boom" in body["result"]["content"][0]["text"]
    finally:
        bg.close()


def test_in_flight_counts_running_calls() -> None:
    fake = _FakeExecutor()
    fake.gate = threading.Event()
    bg = BackgroundTurnToolServer.start(TurnToolServer(fake))
    try:
        done: list[dict[str, Any]] = []

        def _call() -> None:
            r = httpx.post(
                bg.handle.url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "Read", "arguments": {"file_path": "slow.py"}},
                },
                headers=_auth_headers(bg.handle.token),
                timeout=5.0,
            )
            done.append(r.json())

        t = threading.Thread(target=_call)
        t.start()
        assert fake.entered.wait(timeout=2.0)
        assert bg.handle.in_flight == 1
        fake.gate.set()
        t.join(timeout=2.0)
        assert bg.handle.in_flight == 0
        assert len(done) == 1
    finally:
        bg.close()


def test_close_frees_port_and_refuses_new_calls() -> None:
    fake = _FakeExecutor()
    bg = BackgroundTurnToolServer.start(TurnToolServer(fake))
    url = bg.handle.url
    headers = _auth_headers(bg.handle.token)

    bg.handle.refuse_calls("turn budget exhausted")
    refused = httpx.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "Read", "arguments": {"file_path": "a.py"}},
        },
        headers=headers,
        timeout=2.0,
    ).json()["result"]
    assert refused["isError"] is True
    assert "turn budget exhausted" in refused["content"][0]["text"]
    assert fake.calls == []

    bg.close()
    with pytest.raises(httpx.ConnectError):
        httpx.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=headers,
            timeout=3.0,
        )


def test_mcp_config_json_is_valid_and_names_server() -> None:
    fake = _FakeExecutor()
    bg = BackgroundTurnToolServer.start(TurnToolServer(fake, server_name="orin"))
    try:
        cfg = json.loads(bg.handle.mcp_config_json())
        srv = cfg["mcpServers"]["orin"]
        assert srv["type"] == "http"
        assert srv["url"] == bg.handle.url
        assert srv["headers"]["Authorization"] == f"Bearer {bg.handle.token}"
    finally:
        bg.close()


def test_server_package_does_not_import_runtime() -> None:
    assert "agentos.agentic" not in Path(server_mod.__file__).read_text(encoding="utf-8")



def test_empty_tool_list_rejects_all_tool_calls() -> None:
    class _EmptyExecutor(_FakeExecutor):
        def list_tools(self) -> Sequence[McpToolSpec]:
            return []

    fake = _EmptyExecutor()
    bg = BackgroundTurnToolServer.start(TurnToolServer(fake))
    try:
        resp = httpx.post(
            bg.handle.url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "Write", "arguments": {"file_path": "pwn.txt"}},
            },
            headers=_auth_headers(bg.handle.token),
            timeout=2.0,
        )
        res = resp.json()["result"]
        assert res["isError"] is True
        assert fake.calls == []
    finally:
        bg.close()


def test_rejects_ipv6_or_non_ipv4_loopback_bind_and_invalid_jsonrpc_shapes() -> None:
    fake = _FakeExecutor()
    with pytest.raises(ValueError):
        TurnToolServer(fake, host="::1")

    bg = BackgroundTurnToolServer.start(TurnToolServer(fake))
    try:
        headers = _auth_headers(bg.handle.token)
        # Empty object is not a valid JSON-RPC notification or request
        r_empty = httpx.post(bg.handle.url, json={}, headers=headers, timeout=2.0)
        assert r_empty.status_code == 400

        # Oversized batch (> 64 items) is rejected
        r_batch = httpx.post(
            bg.handle.url,
            json=[{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(65)],
            headers=headers,
            timeout=2.0,
        )
        assert r_batch.status_code == 400
    finally:
        bg.close()
