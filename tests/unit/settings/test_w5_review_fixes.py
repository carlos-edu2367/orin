"""W5 review fixes (part 1): regression tests for the findings in docs/superpowers/reviews/2026-10-19-w5-review.md."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentos.mcp.file_config import load_file_servers
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_user_dir, workspace_grants_file


def _write(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _repo_with_a_server(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _write(root / ".mcp.json", {"mcpServers": {
        "helper": {"command": "powershell", "args": ["-NoProfile", "-Command", "Start-Process calc"]},
    }})
    return root


def _servers(root: Path) -> tuple[list[str], list[str]]:
    settings = load_effective_settings(root, trusted=True)
    found = load_file_servers(workspace_root=root, trusted=True, settings=settings.data, environ={}, personal=True)
    return [server.name for server in found.servers], [server.name for server in found.pending]


# W5-R1: a repository's own settings never approve its .mcp.json servers; the person's layers still do.


def test_w5_r1_repository_cannot_approve_its_own_mcp_servers(tmp_path: Path) -> None:
    root = _repo_with_a_server(tmp_path)
    _write(root / ".claude" / "settings.json", {"enableAllProjectMcpServers": True, "enabledMcpjsonServers": ["helper"]})
    settings = load_effective_settings(root, trusted=True)
    assert settings.get("enableAllProjectMcpServers") is None
    assert settings.get("enabledMcpjsonServers") is None
    assert _servers(root) == ([], ["helper"])


def test_w5_r1_repository_local_settings_cannot_approve_either(tmp_path: Path) -> None:
    root = _repo_with_a_server(tmp_path)
    _write(root / ".claude" / "settings.local.json", {"enableAllProjectMcpServers": True})
    _write(root / ".orin" / "settings.local.json", {"enabledMcpjsonServers": ["helper"]})
    assert _servers(root) == ([], ["helper"])


def test_w5_r1_the_persons_own_layers_still_approve(tmp_path: Path) -> None:
    root = _repo_with_a_server(tmp_path)
    _write(orin_user_dir() / "settings.json", {"enabledMcpjsonServers": ["helper"]})
    assert _servers(root) == (["helper"], [])


def test_w5_r1_the_persons_workspace_grants_file_still_approves(tmp_path: Path) -> None:
    root = _repo_with_a_server(tmp_path)
    _write(workspace_grants_file(root), {"enableAllProjectMcpServers": True})
    assert _servers(root) == (["helper"], [])


def test_w5_r1_a_repository_may_still_disable_its_servers(tmp_path: Path) -> None:
    root = _repo_with_a_server(tmp_path)
    _write(orin_user_dir() / "settings.json", {"enableAllProjectMcpServers": True})
    _write(root / ".claude" / "settings.json", {"disabledMcpjsonServers": ["helper"]})
    assert _servers(root) == ([], [])
