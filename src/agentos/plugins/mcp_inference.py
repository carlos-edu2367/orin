"""Best-effort inference of an MCP server's launch command from its own
repository config — only structured files are read (``mcp.json``/
``smithery.json``, ``package.json``, ``pyproject.toml``); free-text sources
like README prose are deliberately not parsed, since a wrong guess there
would be presented to the user as if it were reliable.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tomllib


@dataclass(frozen=True, slots=True)
class McpLaunchGuess:
    display_name: str
    transport: str | None  # "stdio" | "http" | None
    command: str | None
    args: tuple[str, ...]
    url: str | None
    secret_names: tuple[str, ...]
    confidence: str  # "structured" | "none"


def infer_mcp_launch(path: Path, *, suggested_name: str) -> McpLaunchGuess:
    guess = _from_mcp_config(path, suggested_name) or _from_package_json(path, suggested_name) or _from_pyproject(path, suggested_name)
    if guess is not None:
        return guess
    return McpLaunchGuess(suggested_name, None, None, (), None, (), "none")


def _from_mcp_config(path: Path, suggested_name: str) -> McpLaunchGuess | None:
    for filename in ("mcp.json", "smithery.json"):
        candidate = path / filename
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entry = _first_server_entry(data)
        if entry is None:
            continue
        command = entry.get("command")
        url = entry.get("url")
        env = entry.get("env")
        secret_names = tuple(str(key) for key in env.keys()) if isinstance(env, dict) else ()
        if isinstance(command, str) and command.strip():
            args = tuple(str(item) for item in entry.get("args") or ())
            return McpLaunchGuess(suggested_name, "stdio", command.strip(), args, None, secret_names, "structured")
        if isinstance(url, str) and url.strip():
            return McpLaunchGuess(suggested_name, "http", None, (), url.strip(), secret_names, "structured")
    return None


def _first_server_entry(data: object) -> dict | None:
    if not isinstance(data, dict):
        return None
    servers = data.get("mcpServers")
    if isinstance(servers, dict) and servers:
        first = next(iter(servers.values()))
        return first if isinstance(first, dict) else None
    if "command" in data or "url" in data:
        return data
    return None


def _from_package_json(path: Path, suggested_name: str) -> McpLaunchGuess | None:
    candidate = path / "package.json"
    if not candidate.is_file():
        return None
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("bin"):
        return None
    name = str(data.get("name") or "").strip()
    if not name:
        return None
    return McpLaunchGuess(suggested_name, "stdio", "npx", ("-y", name), None, (), "structured")


def _from_pyproject(path: Path, suggested_name: str) -> McpLaunchGuess | None:
    candidate = path / "pyproject.toml"
    if not candidate.is_file():
        return None
    try:
        data = tomllib.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    project = data.get("project") if isinstance(data, dict) else None
    poetry = ((data.get("tool") or {}).get("poetry") if isinstance(data, dict) else None) or {}
    scripts = (project or {}).get("scripts") if isinstance(project, dict) else None
    if not scripts and not poetry.get("scripts"):
        return None
    name = str((project or {}).get("name") or poetry.get("name") or "").strip()
    if not name:
        return None
    return McpLaunchGuess(suggested_name, "stdio", "uvx", (name,), None, (), "structured")


__all__ = ["McpLaunchGuess", "infer_mcp_launch"]
