"""W5: MCP servers written in files — scopes, approval, trust, variables and the allow/deny lists."""
from __future__ import annotations

import json
from pathlib import Path

from agentos.mcp.file_config import FileServer, expand, load_file_servers, parse_server, policy_refusal
from agentos.settings.paths import claude_user_config_file, orin_managed_dir, orin_user_dir, workspace_grants_file


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _stdio(arg: str) -> dict:
    return {"command": "node", "args": [arg]}


def _load(root: Path, *, trusted: bool = True, settings: dict | None = None, environ: dict | None = None, personal: bool = True):
    return load_file_servers(workspace_root=root, trusted=trusted, settings=settings or {}, environ=environ or {}, personal=personal)


def _names(servers) -> list[str]:
    return sorted(server.name for server in servers)


def test_project_servers_need_trust_and_approval(tmp_path: Path) -> None:
    _write(tmp_path / ".mcp.json", {"mcpServers": {"docs": _stdio("docs.js"), "web": {"type": "http", "url": "https://mcp.example.com/mcp"}}})
    untrusted = _load(tmp_path, trusted=False)
    assert untrusted.servers == [] and untrusted.pending == [] and untrusted.project_awaiting_trust is True
    waiting = _load(tmp_path)
    assert waiting.servers == [] and _names(waiting.pending) == ["docs", "web"]
    one = _load(tmp_path, settings={"enabledMcpjsonServers": ["docs"]})
    assert _names(one.servers) == ["docs"] and _names(one.pending) == ["web"]
    assert one.servers[0].scope == "project" and one.servers[0].cwd == str(tmp_path)
    everything = _load(tmp_path, settings={"enableAllProjectMcpServers": True, "disabledMcpjsonServers": ["web"]})
    assert _names(everything.servers) == ["docs"] and everything.pending == []


def test_claude_codes_per_folder_choices_count(tmp_path: Path) -> None:
    _write(tmp_path / ".mcp.json", {"mcpServers": {"web": {"type": "http", "url": "https://mcp.example.com/mcp"}}})
    _write(claude_user_config_file(), {"projects": {str(tmp_path).replace("\\", "/"): {"enabledMcpjsonServers": ["web"]}}})
    assert _names(_load(tmp_path).servers) == ["web"]


def test_scopes_resolve_local_over_project_over_user(tmp_path: Path) -> None:
    _write(claude_user_config_file(), {"mcpServers": {"tools": _stdio("claude-user"), "only-claude": _stdio("x")}})
    _write(orin_user_dir() / "mcp.json", {"mcpServers": {"tools": _stdio("orin-user")}})
    found = _load(tmp_path)
    assert {server.name: server.args for server in found.servers} == {"tools": ("orin-user",), "only-claude": ("x",)}
    _write(tmp_path / ".mcp.json", {"mcpServers": {"tools": _stdio("project")}})
    found = _load(tmp_path, settings={"enabledMcpjsonServers": ["tools"]})
    assert [server.args for server in found.servers if server.name == "tools"] == [("project",)]
    _write(workspace_grants_file(tmp_path).parent / "mcp.json", {"mcpServers": {"tools": _stdio("local")}})
    found = _load(tmp_path, settings={"enabledMcpjsonServers": ["tools"]})
    assert [(server.args, server.scope) for server in found.servers if server.name == "tools"] == [(("local",), "local")]


def test_a_managed_file_is_the_only_source(tmp_path: Path) -> None:
    _write(orin_user_dir() / "mcp.json", {"mcpServers": {"mine": _stdio("x")}})
    _write(orin_managed_dir() / "managed-mcp.json", {"mcpServers": {"corp": {"type": "http", "url": "https://corp.example.com/mcp"}}})
    found = _load(tmp_path)
    assert found.managed_only is True and _names(found.servers) == ["corp"] and found.servers[0].scope == "managed"


def test_a_shared_orin_reads_only_the_managed_file(tmp_path: Path) -> None:
    _write(orin_user_dir() / "mcp.json", {"mcpServers": {"mine": _stdio("x")}})
    _write(tmp_path / ".mcp.json", {"mcpServers": {"repo": _stdio("y")}})
    found = _load(tmp_path, settings={"enableAllProjectMcpServers": True}, personal=False)
    assert found.servers == [] and found.pending == []


def test_variables_are_expanded(tmp_path: Path) -> None:
    assert expand("Bearer ${TOKEN}", {"TOKEN": "abc"}) == "Bearer abc"
    assert expand("${PORT:-8080}", {}) == "8080" and expand("${PORT:-8080}", {"PORT": ""}) == "8080"
    _write(orin_user_dir() / "mcp.json", {"mcpServers": {
        "api": {"type": "http", "url": "https://${HOST}/mcp", "headers": {"Authorization": "Bearer ${TOKEN}"}},
        "broken": {"type": "http", "url": "https://${MISSING}/mcp"},
        "sneaky": {"command": "node", "args": ["${ORIN_SECRET_KEY:-x}"]},
    }})
    found = _load(tmp_path, environ={"HOST": "mcp.example.com", "TOKEN": "abc", "ORIN_SECRET_KEY": "k"})
    assert _names(found.servers) == ["api"]
    assert found.servers[0].url == "https://mcp.example.com/mcp" and dict(found.servers[0].headers) == {"Authorization": "Bearer abc"}
    reasons = " ".join(why for _, why in found.errors)
    assert "MISSING is not set" in reasons and "Orin's own variables" in reasons and "abc" not in reasons


def test_the_allow_and_deny_lists(tmp_path: Path) -> None:
    common = {"command_line": ("npx", "-y", "srv"), "url": None}
    assert policy_refusal("a", settings={"deniedMcpServers": [{"serverName": "a"}]}, **common) is not None
    assert policy_refusal("a", settings={"allowedMcpServers": []}, **common) is not None
    assert policy_refusal("a", settings={"allowedMcpServers": [{"serverName": "a"}]}, **common) is None
    # a command entry in the allow list means a stdio server must match a command, not just its name
    assert policy_refusal("a", settings={"allowedMcpServers": [{"serverName": "a"}, {"serverCommand": ["npx", "other"]}]}, **common) is not None
    assert policy_refusal("a", settings={"allowedMcpServers": [{"serverCommand": ["npx", "-y", "srv"]}]}, **common) is None
    remote = {"command_line": None, "url": "https://api.corp.example.com/mcp"}
    assert policy_refusal("r", settings={"allowedMcpServers": [{"serverUrl": "https://*.corp.example.com/*"}]}, **remote) is None
    assert policy_refusal("r", settings={"allowedMcpServers": [{"serverUrl": "https://other.example.com/*"}]}, **remote) is not None
    assert policy_refusal("r", settings={"deniedMcpServers": [{"serverUrl": "https://*"}], "allowedMcpServers": [{"serverName": "r"}]}, **remote) is not None
    assert policy_refusal("Display", aliases=("slug",), settings={"deniedMcpServers": [{"serverName": "slug"}]}, **remote) is not None
    _write(orin_user_dir() / "mcp.json", {"mcpServers": {"keep": _stdio("k"), "drop": _stdio("d")}})
    found = _load(tmp_path, settings={"deniedMcpServers": [{"serverName": "drop"}]})
    assert _names(found.servers) == ["keep"] and any("deniedMcpServers" in why for _, why in found.errors)


def test_bad_entries_are_reported_and_the_rest_load(tmp_path: Path) -> None:
    (orin_user_dir()).mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "mcp.json").write_text("{not json", encoding="utf-8")
    _write(claude_user_config_file(), {"mcpServers": {
        "good": _stdio("g"), "list": [], "kind": {"type": "carrier-pigeon", "url": "https://x"},
        "ws": {"type": "ws", "url": "https://wrong.example.com"}, "nocmd": {"type": "stdio"},
    }})
    found = _load(tmp_path)
    assert _names(found.servers) == ["good"]
    assert len(found.errors) == 5


def test_two_names_with_one_tool_prefix_keep_the_first(tmp_path: Path) -> None:
    _write(orin_user_dir() / "mcp.json", {"mcpServers": {"My Server": _stdio("a"), "my-server": _stdio("b")}})
    found = _load(tmp_path)
    assert len(found.servers) == 1 and any("tool prefix" in why for _, why in found.errors)


def test_the_fingerprint_follows_the_values() -> None:
    first = parse_server("s", {"command": "node", "env": {"K": "1"}}, scope="user", source="x", environ={})
    second = parse_server("s", {"command": "node", "env": {"K": "2"}}, scope="user", source="x", environ={})
    assert isinstance(first, FileServer) and first.fingerprint() != second.fingerprint()
    assert first.fingerprint() == parse_server("s", {"command": "node", "env": {"K": "1"}}, scope="local", source="y", environ={}).fingerprint()
