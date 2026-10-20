"""W6 Task 18: MCP servers from orin code's command line, and SDK servers relayed through the host."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.mcp.file_config import McpConfigError, load_file_servers, parse_server
from agentos.mcp.transport_sdk import SdkRelayError, SdkRelayTransport, register_relay, unregister_relay
from agentos.settings.paths import orin_managed_dir, orin_user_dir

CLI = {"echo": {"command": "python", "args": ["e.py"]}, "calc": {"type": "sdk", "name": "calc"}}


def _user_servers(servers: dict) -> None:
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_the_command_lines_servers_join_and_win(tmp_path: Path) -> None:
    _user_servers({"echo": {"command": "node", "args": ["old.js"]}, "files": {"command": "node"}})
    found = load_file_servers(workspace_root=tmp_path, trusted=False, settings={}, cli=CLI)
    by_name = {server.name: server for server in found.servers}
    assert by_name["echo"].scope == "cli" and by_name["echo"].args == ("e.py",)
    assert by_name["calc"].transport == "sdk" and by_name["files"].scope == "user"


def test_strict_uses_only_the_command_lines_servers(tmp_path: Path) -> None:
    _user_servers({"files": {"command": "node"}})
    found = load_file_servers(workspace_root=tmp_path, trusted=False, settings={}, cli=CLI, strict=True)
    assert sorted(server.name for server in found.servers) == ["calc", "echo"]


def test_a_managed_configuration_still_decides_alone(tmp_path: Path) -> None:
    orin_managed_dir().mkdir(parents=True, exist_ok=True)
    (orin_managed_dir() / "managed-mcp.json").write_text(json.dumps({"mcpServers": {"corp": {"url": "https://mcp.example.com"}}}), encoding="utf-8")
    found = load_file_servers(workspace_root=tmp_path, trusted=False, settings={}, cli=CLI, strict=True)
    assert [server.name for server in found.servers] == ["corp"]
    assert any("command line" in why for _, why in found.errors)


def test_only_the_command_line_may_declare_an_sdk_server() -> None:
    for scope in ("user", "project", "local", "managed"):
        with pytest.raises(McpConfigError):
            parse_server("calc", {"type": "sdk"}, scope=scope, source="x", environ={})
    assert parse_server("calc", {"type": "sdk"}, scope="cli", source="x", environ={}).transport == "sdk"


def test_the_relay_carries_requests_and_notifications() -> None:
    asked: list[tuple] = []

    def ask(kind, request, timeout):
        asked.append((kind, request))
        return {"mcp_response": {"jsonrpc": "2.0", "id": request["message"].get("id"), "result": {"ok": True}}}

    register_relay("turn-1", ask)
    try:
        transport = SdkRelayTransport(server_name="calc", relay_key="turn-1")
        transport.open()
        assert transport.send({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})["result"] == {"ok": True}
        assert transport.send({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
        assert [item[0] for item in asked] == ["mcp_message", "mcp_message"] and asked[0][1]["server_name"] == "calc"
    finally:
        unregister_relay("turn-1")


def test_a_missing_or_silent_host_is_an_error() -> None:
    with pytest.raises(SdkRelayError):
        SdkRelayTransport(server_name="calc", relay_key="nobody").open()
    register_relay("turn-2", lambda *_: None)
    try:
        with pytest.raises(SdkRelayError):
            SdkRelayTransport(server_name="calc", relay_key="turn-2").send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    finally:
        unregister_relay("turn-2")
    register_relay("turn-3", lambda *_: {"_error": "host crashed"})
    try:
        with pytest.raises(SdkRelayError, match="host crashed"):
            SdkRelayTransport(server_name="calc", relay_key="turn-3").send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    finally:
        unregister_relay("turn-3")
