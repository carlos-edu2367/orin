"""W6 Task 19: `orin code mcp` (configuration files and `serve`)."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentos.cli_code.commands.mcp import scope_file
from agentos.cli_code.commands.mcp_serve import handle
from agentos.cli_code.main import main
from agentos.settings.paths import orin_user_dir, workspace_grants_file


def _run(argv: list[str], cwd: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    monkeypatch.chdir(cwd)
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdin=io.StringIO(""), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_mcp_add_list_get_and_remove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _ = _run(["mcp", "add", "-s", "user", "-e", "TOKEN=abc", "files", "node", "server.js", "--root", "."], tmp_path, monkeypatch)
    assert code == 0 and "user" in out
    stored = json.loads((orin_user_dir() / "mcp.json").read_text(encoding="utf-8"))
    assert stored["mcpServers"]["files"] == {"command": "node", "args": ["server.js", "--root", "."], "env": {"TOKEN": "abc"}}
    assert _run(["mcp", "add", "-H", "Authorization: Bearer x", "remote", "https://mcp.example.com/mcp"], tmp_path, monkeypatch)[0] == 0
    local = json.loads(scope_file("local", tmp_path).read_text(encoding="utf-8"))
    assert local["mcpServers"]["remote"] == {"type": "http", "url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer x"}}
    code, out, _ = _run(["mcp", "add-json", "-s", "project", "ws", '{"type": "ws", "url": "wss://x.example.com"}'], tmp_path, monkeypatch)
    assert code == 0 and (tmp_path / ".mcp.json").is_file()
    code, out, _ = _run(["mcp", "list"], tmp_path, monkeypatch)
    assert "files: node server.js --root . [user]" in out and "remote:" in out and "[local]" in out
    code, out, _ = _run(["mcp", "get", "files"], tmp_path, monkeypatch)
    assert json.loads(out)["scope"] == "user"
    assert _run(["mcp", "remove", "files"], tmp_path, monkeypatch)[0] == 0
    assert "files" not in json.loads((orin_user_dir() / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert _run(["mcp", "remove", "nothing"], tmp_path, monkeypatch)[0] == 1


@pytest.mark.parametrize("argv", [
    ["mcp", "add", "x" * 300, "node"], ["mcp", "add-json", "x", "[1]"], ["mcp", "add-json", "x", '{"type": "sdk"}'],
    ["mcp", "login", "srv"], ["mcp", "add", "-e", "NOEQUALS", "x", "node"],
])
def test_mcp_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    code, _, err = _run(argv, tmp_path, monkeypatch)
    assert code == 1 and err.startswith("Error: ")


def test_reset_project_choices_forgets_approvals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    grants = workspace_grants_file(tmp_path)
    grants.parent.mkdir(parents=True, exist_ok=True)
    grants.write_text(json.dumps({"enabledMcpjsonServers": ["a"], "enableAllProjectMcpServers": True, "permissions": {"allow": ["Read"]}}), encoding="utf-8")
    assert _run(["mcp", "reset-project-choices"], tmp_path, monkeypatch)[0] == 0
    assert json.loads(grants.read_text(encoding="utf-8")) == {"permissions": {"allow": ["Read"]}}


def test_mcp_serve_offers_orins_tools_without_asking(tmp_path: Path) -> None:
    from agentos.cli_code.commands.mcp_serve import _toolset

    (tmp_path / "notes.md").write_text("hello serve\n", encoding="utf-8")
    toolset = _toolset(tmp_path)
    assert handle(toolset, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]["serverInfo"]["name"] == "orin-code"
    names = {tool["name"] for tool in handle(toolset, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
    assert {"Read", "Grep", "Glob", "Write", "Edit", "Bash"} <= names
    read = handle(toolset, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "Read", "arguments": {"file_path": "notes.md"}}})
    assert read["result"]["isError"] is False and "hello serve" in read["result"]["content"][0]["text"]
    write = handle(toolset, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "Write", "arguments": {"file_path": "x.txt", "content": "x"}}})
    assert write["result"]["isError"] is True and not (tmp_path / "x.txt").exists()  # dontAsk: nobody to ask
    assert handle(toolset, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "Agent"}})["error"]["code"] == -32602
    assert handle(toolset, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_mcp_serve_speaks_stdio(tmp_path: Path) -> None:
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})]
    done = subprocess.run([sys.executable, "-m", "agentos.cli_code.main", "mcp", "serve"], cwd=tmp_path, input="\n".join(lines) + "\n",
                          capture_output=True, text=True, encoding="utf-8", timeout=120, env=os.environ.copy())
    answers = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
    assert done.returncode == 0 and [item["id"] for item in answers] == [1, 2]
