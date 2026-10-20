"""``orin code mcp``: the MCP servers in Orin's and Claude Code's configuration files, from a terminal.

Reading covers every scope Orin loads (user, local, project, managed; see ``mcp.file_config``). Writing always goes to
Orin's own files (decision D2): ``user`` -> ``~/.orin/mcp.json``, ``local`` -> the per-folder file in
``~/.orin/workspaces``, ``project`` -> ``.mcp.json`` in the folder (shared with Claude Code by design). Servers set up in
Orin's interface (and their OAuth sign-in) are managed there: ``login`` and ``logout`` say so instead of guessing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, TextIO

from agentos.mcp.file_config import McpConfigError, load_file_servers, parse_server
from agentos.settings.paths import orin_user_dir, workspace_grants_file
from agentos.settings.writer import update_settings_file

from ..args import CliUsageError
from ..errors import CliRuntimeError

SCOPES = ("local", "project", "user")
_PROJECT_CHOICES = ("enabledMcpjsonServers", "disabledMcpjsonServers", "enableAllProjectMcpServers")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def scope_file(scope: str, cwd: Path) -> Path:
    if scope == "user":
        return orin_user_dir() / "mcp.json"
    if scope == "project":
        return cwd / ".mcp.json"
    return workspace_grants_file(cwd).parent / "mcp.json"


def _describe(server) -> str:
    if server.transport == "stdio":
        return " ".join((server.command, *server.args))
    return f"{server.url} ({server.transport})" if server.url else server.transport


def _trusted(cwd: Path) -> bool:
    from agentos.permissions.trust import WorkspaceTrustStore

    try:
        return WorkspaceTrustStore().is_trusted("local-user", cwd)
    except Exception:  # noqa: BLE001 - unknown trust means untrusted
        return False


def _check(name: str, entry: dict[str, Any]) -> None:
    try:
        parse_server(name, entry, scope="user", source="orin code mcp", environ={})
    except McpConfigError as error:
        raise CliUsageError(f"{name}: {error}") from error


def _write(scope: str, cwd: Path, name: str, entry: dict[str, Any] | None) -> Path:
    path = scope_file(scope, cwd)

    def mutate(data: dict[str, Any]) -> None:
        servers = data.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            servers = data["mcpServers"] = {}
        if entry is None:
            if name not in servers:
                raise CliRuntimeError(f"no MCP server named {name!r} in the {scope} scope ({path})")
            del servers[name]
        else:
            servers[name] = entry

    update_settings_file(path, mutate)
    return path


def _pairs(values: list[str], separator: str, flag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for value in values:
        key, found, rest = value.partition(separator)
        if not found or not key.strip():
            raise CliUsageError(f"{flag} takes NAME{separator}VALUE, not {value!r}")
        out[key.strip()] = rest.strip()
    return out


def run(argv: list[str], *, cwd: Path, stdout: TextIO, stderr: TextIO) -> int:
    parser = _Parser(prog="orin code mcp", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    get = commands.add_parser("get")
    get.add_argument("name")
    add = commands.add_parser("add")
    add.add_argument("-s", "--scope", choices=SCOPES, default="local")
    add.add_argument("-t", "--transport", choices=("stdio", "http", "sse", "ws"), default=None)
    add.add_argument("-e", "--env", action="append", default=[])
    add.add_argument("-H", "--header", action="append", default=[])
    add.add_argument("name")
    add.add_argument("target")
    add.add_argument("args", nargs=argparse.REMAINDER)
    add_json = commands.add_parser("add-json")
    add_json.add_argument("-s", "--scope", choices=SCOPES, default="local")
    add_json.add_argument("name")
    add_json.add_argument("json")
    remove = commands.add_parser("remove")
    remove.add_argument("-s", "--scope", choices=SCOPES, default=None)
    remove.add_argument("name")
    commands.add_parser("reset-project-choices")
    commands.add_parser("serve")
    for verb in ("login", "logout"):
        commands.add_parser(verb).add_argument("name", nargs="?")
    options = parser.parse_args(argv)

    if options.command in {"login", "logout"}:
        raise CliRuntimeError("MCP sign-in (OAuth) is done in Orin's interface: Settings > MCP")
    if options.command == "serve":
        from .mcp_serve import serve

        return serve(cwd=cwd)
    if options.command == "list":
        found = load_file_servers(workspace_root=cwd, trusted=_trusted(cwd), settings={})
        for server in found.servers:
            stdout.write(f"{server.name}: {_describe(server)} [{server.scope}]\n")
        for server in found.pending:
            stdout.write(f"{server.name}: {_describe(server)} [project, not approved]\n")
        for where, why in found.errors:
            stderr.write(f"{where}: {why}\n")
        if found.project_awaiting_trust:
            stderr.write("this folder is not trusted yet, so its .mcp.json servers are not read\n")
        if not found.servers and not found.pending:
            stdout.write("No MCP servers in configuration files.\n")
        return 0
    if options.command == "get":
        found = load_file_servers(workspace_root=cwd, trusted=_trusted(cwd), settings={})
        match = next((server for server in [*found.servers, *found.pending] if server.name == options.name), None)
        if match is None:
            raise CliRuntimeError(f"no MCP server named {options.name!r}")
        stdout.write(json.dumps({"name": match.name, "scope": match.scope, "source": match.source, "type": match.transport,
                                 "command": match.command or None, "args": list(match.args), "url": match.url or None}, indent=2) + "\n")
        return 0
    if options.command in {"add", "add-json"}:
        if options.command == "add":
            target = options.target
            kind = options.transport or ("http" if target.startswith(("http://", "https://")) else "ws" if target.startswith(("ws://", "wss://")) else "stdio")
            if kind == "stdio":
                entry: dict[str, Any] = {"command": target, "args": [item for item in options.args if item != "--"]}
                if options.env:
                    entry["env"] = _pairs(options.env, "=", "-e")
            else:
                entry = {"type": kind, "url": target}
                if options.header:
                    entry["headers"] = _pairs(options.header, ":", "-H")
        else:
            try:
                entry = json.loads(options.json)
            except ValueError as error:
                raise CliUsageError("add-json needs a JSON object") from error
            if not isinstance(entry, dict):
                raise CliUsageError("add-json needs a JSON object")
        _check(options.name, entry)
        path = _write(options.scope, cwd, options.name, entry)
        stdout.write(f"Added MCP server {options.name} to {path} ({options.scope})\n")
        if options.scope == "project":
            stdout.write("A project server runs only after the folder is trusted and the server approved.\n")
        return 0
    if options.command == "remove":
        scopes = [options.scope] if options.scope else list(SCOPES)
        removed = []
        for scope in scopes:
            try:
                removed.append(_write(scope, cwd, options.name, None))
            except CliRuntimeError:
                continue
        if not removed:
            raise CliRuntimeError(f"no MCP server named {options.name!r} in {', '.join(scopes)}")
        for path in removed:
            stdout.write(f"Removed MCP server {options.name} from {path}\n")
        return 0
    # reset-project-choices: forget the approvals and refusals of this folder's .mcp.json servers (Orin's local file).
    path = workspace_grants_file(cwd)

    def forget(data: dict[str, Any]) -> None:
        for key in _PROJECT_CHOICES:
            data.pop(key, None)

    update_settings_file(path, forget)
    local_servers = scope_file("local", cwd)
    if local_servers.is_file():
        update_settings_file(local_servers, forget)
    stdout.write(f"This folder's .mcp.json servers will be asked about again ({path})\n")
    return 0


__all__ = ["SCOPES", "run", "scope_file"]
