"""MCP servers from configuration files, per scope, and the approval of a project's servers (W7 Batch F).

The settings editor shows which configuration-file servers are in force for a folder (user, project, local, managed,
in Claude Code's files and Orin's), which of the project's servers wait for the person's approval, and lets the person
approve, deny or reset one. Approvals are written only to the per-folder grants file outside the repository
(``~/.orin/workspaces/<key>/settings.local.json``): a repository's own files can never approve its servers (W5-R1).

Nothing here returns a server's environment, headers or an expanded command line: ``${VAR}`` expansion puts the
server process's secrets into those values. A project server is shown as its repository file writes it (what the
person is approving); other servers show only the program or the address's scheme and host.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from agentos.redaction import redact_text

MAX_SHOWN_CHARS = 400
APPROVAL_KEYS = ("enabledMcpjsonServers", "disabledMcpjsonServers")


class McpApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=200)
    server: str = Field(min_length=1, max_length=200)
    decision: Literal["approve", "deny", "reset"]


def _address(url: str) -> str:
    """An address without credentials, path, query or fragment (a URL's path and query often hold tokens)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if not parts.scheme or not parts.hostname:
        return ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def _program(command: str) -> str:
    return Path(command.replace("\\", "/")).name if command else ""


def project_server_files(root: Path) -> list[Path]:
    return [root / ".mcp.json", root / ".orin" / "mcp.json"]


def _raw_project_servers(root: Path) -> dict[str, dict[str, Any]]:
    """The project's servers as its files write them (no expansion); the later file wins, as the loader does."""
    servers: dict[str, dict[str, Any]] = {}
    for path in project_server_files(root):
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, ValueError):
            continue
        entries = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(entries, dict):
            for name, raw in entries.items():
                if isinstance(name, str) and isinstance(raw, dict):
                    servers[name] = {**raw, "_source": str(path)}
    return servers


def _shown_raw(raw: dict[str, Any]) -> str:
    """A project server's command or address as the repository writes it, with secrets that look like secrets masked."""
    if isinstance(raw.get("url"), str):
        text = raw["url"]
    else:
        args = raw.get("args") if isinstance(raw.get("args"), list) else []
        text = " ".join([str(raw.get("command") or ""), *(str(item) for item in args)]).strip()
    return redact_text(text)[:MAX_SHOWN_CHARS]


def mcp_report(root: Path | None, trusted: bool, *, personal: bool) -> dict[str, Any]:
    from agentos.mcp.file_config import McpConfigError, load_file_servers
    from agentos.settings import load_effective_settings
    from agentos.settings.paths import workspace_grants_file

    settings = load_effective_settings(root, trusted=trusted) if root is not None else load_effective_settings(None)
    try:
        found = load_file_servers(workspace_root=root, trusted=trusted, settings=settings.data, personal=personal)
    except McpConfigError as error:
        return {"servers": [], "pending": [], "errors": [{"where": "mcp", "message": redact_text(str(error))[:300]}],
                "managed_only": False, "awaiting_trust": False, "folder": str(root) if root else None, "trusted": trusted, "choices": {}}
    raw_project = _raw_project_servers(root) if root is not None and trusted else {}

    def describe(server, state: str) -> dict[str, Any]:
        raw = raw_project.get(server.name) if server.scope == "project" else None
        if raw is not None:
            shown = _shown_raw(raw)
        elif server.url:
            shown = _address(server.url)
        else:
            shown = _program(server.command)
        return {"name": server.name, "scope": server.scope, "source": server.source, "transport": server.transport, "shown": shown, "state": state}

    servers = [describe(server, "on") for server in found.servers]
    pending = [describe(server, "pending") for server in found.pending]
    on = {server.name for server in found.servers if server.scope == "project"}
    waiting = {server.name for server in found.pending}
    denied = [
        {"name": name, "scope": "project", "source": raw["_source"], "transport": str(raw.get("type") or ("http" if raw.get("url") else "stdio")),
         "shown": _shown_raw(raw), "state": "off"}
        for name, raw in raw_project.items() if name not in on and name not in waiting
    ]
    choices: dict[str, list[str]] = {}
    if root is not None:
        try:
            grants = json.loads(workspace_grants_file(root).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            grants = {}
        if isinstance(grants, dict):
            choices = {key: [item for item in grants.get(key, []) if isinstance(item, str)] for key in APPROVAL_KEYS if isinstance(grants.get(key), list)}
    return {
        "folder": str(root) if root is not None else None, "trusted": trusted, "managed_only": found.managed_only,
        "awaiting_trust": found.project_awaiting_trust, "servers": servers + denied, "pending": pending,
        "errors": [{"where": redact_text(where)[:300], "message": redact_text(message)[:300]} for where, message in found.errors],
        "choices": choices,
    }


def change_mcp_approval(root: Path, trusted: bool, change: McpApproval) -> None:
    """Approve, deny or forget one of the project's servers, in the per-folder grants file."""
    from agentos.api.settings_routes import SettingsRejected
    from agentos.settings.paths import workspace_grants_file
    from agentos.settings.writer import SettingsWriteError, update_settings_file

    if not trusted:
        raise SettingsRejected("folder_not_trusted", ["Confie na pasta antes de aprovar os servidores do projeto."])
    if change.server not in _raw_project_servers(root):
        raise SettingsRejected("unknown_server", [f"'{change.server[:80]}' não é um servidor do .mcp.json desta pasta."])

    def mutate(data: dict[str, Any]) -> None:
        for key in APPROVAL_KEYS:
            current = [item for item in data.get(key, []) if isinstance(item, str) and item != change.server] if isinstance(data.get(key), list) else []
            wanted = (key == "enabledMcpjsonServers" and change.decision == "approve") or (key == "disabledMcpjsonServers" and change.decision == "deny")
            if wanted:
                current.append(change.server)
            if current:
                data[key] = current
            else:
                data.pop(key, None)

    try:
        update_settings_file(workspace_grants_file(root), mutate)
    except SettingsWriteError as error:
        raise SettingsRejected("settings_unwritable", [redact_text(str(error))[:300]]) from error


__all__ = ["McpApproval", "change_mcp_approval", "mcp_report", "project_server_files"]
