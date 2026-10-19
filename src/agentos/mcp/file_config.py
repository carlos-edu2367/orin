"""MCP servers written in files (W5): the managed, user, local and project scopes, read as Claude Code reads them.

Where each scope lives:

- **managed**: ``managed-mcp.json`` in Orin's and Claude Code's managed folders. When one exists it is the only
  source: no user, local or project server loads (an organisation's exclusive list).
- **user**: ``mcpServers`` in ``~/.orin/mcp.json`` and in Claude Code's ``~/.claude.json``.
- **local**: this workspace's private servers: ``projects[<workspace>].mcpServers`` in ``~/.claude.json`` and Orin's
  ``~/.orin/workspaces/<key>/mcp.json``.
- **project**: the repository's ``.mcp.json`` and ``.orin/mcp.json``. They are read only once the workspace is trusted,
  and a server loads only once the person approved it: ``enableAllProjectMcpServers`` or its name in
  ``enabledMcpjsonServers``, never a name in ``disabledMcpjsonServers``. The approvals come from the effective settings
  and from the same per-workspace entries (Claude Code keeps them in ``projects[<workspace>]``).

A name defined in more than one scope resolves local > project > user (Orin's file wins over Claude Code's within a
scope). ``${VAR}`` and ``${VAR:-default}`` are expanded in the command, arguments, environment, URL and headers; an
unset variable without a default stops that server (it is reported, not loaded). Orin's own variables (``ORIN_*``,
``AGENTOS_*``) are never expanded. ``allowedMcpServers`` / ``deniedMcpServers`` (by name, command or URL pattern) are
checked by ``policy_refusal``, for these servers and for Orin's own.

Nothing here touches the network or starts a process: it only reads files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .models import slugify

MAX_CONFIG_BYTES = 1_000_000
MAX_FILE_SERVERS = 50
MAX_NAME_CHARS = 64
TRANSPORTS = ("stdio", "http", "sse", "ws")
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_PRIVATE_PREFIXES = ("ORIN_", "AGENTOS_")


class McpConfigError(ValueError):
    """A server entry that cannot be used as written."""


@dataclass(frozen=True, slots=True)
class FileServer:
    name: str
    scope: str  # managed | local | project | user
    source: str
    transport: str  # stdio | http | sse | ws
    command: str = ""
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    url: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    cwd: str | None = None

    @property
    def command_line(self) -> tuple[str, ...] | None:
        return (self.command, *self.args) if self.transport == "stdio" else None

    def fingerprint(self) -> str:
        """Identifies this exact configuration (values included) for the discovery cache; never shown."""
        payload = [self.transport, self.command, list(self.args), sorted(self.env), self.url, sorted(self.headers), self.cwd]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(slots=True)
class FileServerSet:
    servers: list[FileServer] = field(default_factory=list)  # approved and allowed: ready to connect
    pending: list[FileServer] = field(default_factory=list)  # project servers awaiting the person's approval
    errors: list[tuple[str, str]] = field(default_factory=list)  # (where, why)
    managed_only: bool = False
    project_awaiting_trust: bool = False


def expand(value: str, environ: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name.upper().startswith(_PRIVATE_PREFIXES):
            raise McpConfigError(f"${{{name}}} is one of Orin's own variables and is never expanded")
        found = environ.get(name)
        if found:
            return found
        if default is not None:
            return default
        if found is None:
            raise McpConfigError(f"the environment variable {name} is not set")
        return found

    return _VARIABLE.sub(replace, str(value))


def _strings(raw: Any, what: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise McpConfigError(f"{what} must be a list of strings")
    return list(raw)


def _string_map(raw: Any, what: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        raise McpConfigError(f"{what} must map names to strings")
    return dict(raw)


def parse_server(name: str, raw: Any, *, scope: str, source: str, environ: Mapping[str, str], cwd: str | None = None) -> FileServer:
    clean = str(name or "").strip()
    if not clean or len(clean) > MAX_NAME_CHARS:
        raise McpConfigError(f"a server name must be 1-{MAX_NAME_CHARS} characters")
    try:
        slugify(clean)
    except ValueError as error:
        raise McpConfigError(str(error)) from error
    if not isinstance(raw, Mapping):
        raise McpConfigError("a server entry must be an object")
    kind = str(raw.get("type") or ("stdio" if "command" in raw else "http" if "url" in raw else "")).strip().lower()
    kind = {"streamable-http": "http", "streamable_http": "http", "websocket": "ws"}.get(kind, kind)
    if kind not in TRANSPORTS:
        raise McpConfigError(f"unknown server type '{kind[:20]}' (expected one of {', '.join(TRANSPORTS)})")
    if kind == "stdio":
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            raise McpConfigError("a stdio server needs a command")
        args = tuple(expand(item, environ) for item in _strings(raw.get("args"), "args"))
        env = tuple(sorted((key, expand(value, environ)) for key, value in _string_map(raw.get("env"), "env").items()))
        return FileServer(clean, scope, source, kind, command=expand(command.strip(), environ), args=args, env=env, cwd=cwd)
    url = raw.get("url")
    if not isinstance(url, str) or not url.strip():
        raise McpConfigError(f"a {kind} server needs a url")
    expanded = expand(url.strip(), environ)
    schemes = ("ws://", "wss://") if kind == "ws" else ("http://", "https://")
    if not expanded.lower().startswith(schemes):
        raise McpConfigError(f"a {kind} server's url must start with {' or '.join(schemes)}")
    headers = tuple(sorted((key, expand(value, environ)) for key, value in _string_map(raw.get("headers"), "headers").items()))
    return FileServer(clean, scope, source, kind, url=expanded, headers=headers)


def _read_json(path: Path, errors: list[tuple[str, str]]) -> Mapping[str, Any] | None:
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > MAX_CONFIG_BYTES:
            errors.append((str(path), "the file is larger than 1 MB"))
            return None
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        errors.append((str(path), f"unreadable: {str(error)[:200]}"))
        return None
    if not isinstance(data, Mapping):
        errors.append((str(path), "the file is not a JSON object"))
        return None
    return data


def _same_folder(key: str, root: Path) -> bool:
    try:
        return os.path.normcase(os.path.normpath(key)) == os.path.normcase(os.path.normpath(str(root)))
    except (TypeError, ValueError):
        return False


def _workspace_entries(claude_config: Mapping[str, Any] | None, root: Path | None) -> list[Mapping[str, Any]]:
    """Claude Code's per-folder entries for this workspace (it may keep the same folder under two spellings)."""
    projects = claude_config.get("projects") if claude_config else None
    if root is None or not isinstance(projects, Mapping):
        return []
    return [entry for key, entry in projects.items() if isinstance(entry, Mapping) and isinstance(key, str) and _same_folder(key, root)]


def _names(raw: Any) -> set[str]:
    return {item for item in raw if isinstance(item, str)} if isinstance(raw, list) else set()


def _collect(data: Mapping[str, Any] | None, *, scope: str, source: str, environ: Mapping[str, str], cwd: str | None,
             into: dict[str, FileServer], errors: list[tuple[str, str]]) -> None:
    servers = data.get("mcpServers") if data else None
    if servers is None:
        return
    if not isinstance(servers, Mapping):
        errors.append((source, "mcpServers must be an object"))
        return
    for name, raw in servers.items():
        try:
            into[str(name).strip()] = parse_server(str(name), raw, scope=scope, source=source, environ=environ, cwd=cwd)
        except McpConfigError as error:
            errors.append((f"{source}: {str(name)[:MAX_NAME_CHARS]}", str(error)))


# W5-R10: a URL and a URL pattern are both normalised before they are compared (see _policy_url).
_DEFAULT_PORTS = {"https": 443, "wss": 443, "http": 80, "ws": 80}
_HOST = re.compile(r"[a-z0-9._-]+")
_PATTERN_HOST = re.compile(r"[a-z0-9._*-]+")


def _resolve_dot_segments(path: str) -> str:
    """RFC 3986 section 5.2.4 for an absolute path: the '.' and '..' segments are resolved."""
    segments = path.split("/")[1:]
    out: list[str] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "..":
            if out:
                out.pop()
            if last:
                out.append("")
        elif segment == ".":
            if last:
                out.append("")
        else:
            out.append(segment)
    return "/" + "/".join(out)


def _policy_url(text: Any, *, pattern: bool) -> tuple[str, str, int | None, str] | None:
    """A URL as the policy compares it: (scheme, host, port, path and query), or None when the policy cannot read it.

    Lower-case scheme and host, the default port dropped, one trailing dot on the host dropped, dot segments resolved.
    A pattern that names no port matches any port (port None), and a pattern that names no path matches any path.
    Fail closed: whitespace, control characters, a backslash, user information, an unknown scheme or an odd host name
    make a URL unreadable.
    """
    if not isinstance(text, str) or any(ord(ch) <= 32 or ord(ch) == 127 or ch == "\\" for ch in text):
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    if scheme not in _DEFAULT_PORTS or "@" in parts.netloc or not host:
        return None
    if host.endswith("."):
        host = host[:-1]
    if not (_PATTERN_HOST if pattern else _HOST).fullmatch(host) or ".." in host or host.startswith(".") or host.endswith("."):
        return None
    if pattern and port is None:
        effective_port: int | None = None
    else:
        effective_port = port if port is not None else _DEFAULT_PORTS[scheme]
    if pattern and not parts.path and not parts.query:
        return scheme, host, effective_port, "*"
    path = (parts.path or "/").replace("%2e", ".").replace("%2E", ".")
    query = f"?{parts.query}" if parts.query else ""
    return scheme, host, effective_port, _resolve_dot_segments(path) + query


def _wildcard(pattern: str, value: str, *, host: bool = False) -> bool:
    """A ``*`` stands for any text, or for host characters only in a host name (never a slash or an @)."""
    filler = "[a-z0-9._-]*" if host else ".*"
    return re.fullmatch(filler.join(re.escape(part) for part in pattern.split("*")), value, re.DOTALL) is not None


def _url_matches(pattern: str, url: str) -> bool:
    wanted = _policy_url(pattern, pattern=True)
    actual = _policy_url(url, pattern=False)
    if wanted is None or actual is None:
        return False
    (w_scheme, w_host, w_port, w_rest), (a_scheme, a_host, a_port, a_rest) = wanted, actual
    return (w_scheme == a_scheme and (w_port is None or w_port == a_port)
            and _wildcard(w_host, a_host, host=True) and _wildcard(w_rest, a_rest))


def policy_refusal(name: str, *, aliases: tuple[str, ...] = (), command_line: tuple[str, ...] | None, url: str | None,
                   settings: Mapping[str, Any]) -> str | None:
    """Why ``allowedMcpServers`` / ``deniedMcpServers`` keep this server off, or None.

    An entry names a server (``serverName``), an exact command line (``serverCommand``) or a URL pattern with ``*``
    (``serverUrl``); ``aliases`` are other names the server answers to (an Orin server's slug). The deny list wins. With an allow list, a stdio server must match one of its commands when it has
    any, and a remote server one of its URLs when it has any; otherwise its name must be listed. An empty allow list
    allows nothing.
    """
    def matches(entry: Any) -> bool:
        if not isinstance(entry, Mapping):
            return False
        if "serverName" in entry:
            return entry["serverName"] in (name, *aliases)
        if "serverCommand" in entry:
            return command_line is not None and isinstance(entry["serverCommand"], list) and tuple(entry["serverCommand"]) == command_line
        if "serverUrl" in entry:
            return url is not None and isinstance(entry["serverUrl"], str) and _url_matches(entry["serverUrl"], url)
        return False

    denied, allowed = settings.get("deniedMcpServers"), settings.get("allowedMcpServers")
    if url is not None and _policy_url(url, pattern=False) is None:
        # A URL the policy cannot read is refused whenever a list it is checked against exists (fail closed).
        if isinstance(denied, list) and denied:
            return "its URL cannot be checked against deniedMcpServers"
        if isinstance(allowed, list):
            return "its URL cannot be checked against allowedMcpServers"
    if isinstance(denied, list) and any(matches(entry) for entry in denied):
        return "it is listed in deniedMcpServers"
    if not isinstance(allowed, list):
        return None
    by_command = [entry for entry in allowed if isinstance(entry, Mapping) and "serverCommand" in entry]
    by_url = [entry for entry in allowed if isinstance(entry, Mapping) and "serverUrl" in entry]
    if command_line is not None and by_command:
        candidates = by_command
    elif url is not None and by_url:
        candidates = by_url
    else:
        candidates = [entry for entry in allowed if isinstance(entry, Mapping) and "serverName" in entry]
    return None if any(matches(entry) for entry in candidates) else "it is not in allowedMcpServers"


def load_file_servers(*, workspace_root: Path | None, trusted: bool, settings: Mapping[str, Any],
                      environ: Mapping[str, str] | None = None, personal: bool = True) -> FileServerSet:
    """The servers to connect, the project servers awaiting approval, and what could not be read.

    ``personal=False`` reads only the managed file: an Orin shared by several people never loads one person's
    (or one repository's) servers for everyone.
    """
    from agentos.settings.paths import (
        claude_managed_dir, claude_user_config_file, orin_managed_dir, orin_user_dir, workspace_grants_file,
    )

    env = dict(os.environ if environ is None else environ)
    found = FileServerSet()
    root = Path(workspace_root) if workspace_root is not None else None
    cwd = str(root) if root is not None else None

    managed: dict[str, FileServer] = {}
    for folder in (claude_managed_dir(), orin_managed_dir()):  # Orin's wins on a shared name
        path = folder / "managed-mcp.json"
        data = _read_json(path, found.errors)
        if data is not None:
            found.managed_only = True
            _collect(data, scope="managed", source=str(path), environ=env, cwd=cwd, into=managed, errors=found.errors)
    if found.managed_only or not personal:
        merged = managed
    else:
        claude_config = _read_json(claude_user_config_file(), found.errors)
        user: dict[str, FileServer] = {}
        _collect(claude_config, scope="user", source=str(claude_user_config_file()), environ=env, cwd=cwd, into=user, errors=found.errors)
        _collect(_read_json(orin_user_dir() / "mcp.json", found.errors), scope="user", source=str(orin_user_dir() / "mcp.json"),
                 environ=env, cwd=cwd, into=user, errors=found.errors)
        local: dict[str, FileServer] = {}
        entries = list(_workspace_entries(claude_config, root))
        for entry in entries:
            _collect(entry, scope="local", source=f"{claude_user_config_file()} (this workspace)", environ=env, cwd=cwd, into=local, errors=found.errors)
        if root is not None:
            local_file = workspace_grants_file(root).parent / "mcp.json"
            local_data = _read_json(local_file, found.errors)
            if local_data is not None:
                entries.append(local_data)
                _collect(local_data, scope="local", source=str(local_file), environ=env, cwd=cwd, into=local, errors=found.errors)
        project: dict[str, FileServer] = {}
        if root is not None:
            files = [root / ".mcp.json", root / ".orin" / "mcp.json"]
            if not trusted:
                found.project_awaiting_trust = any(path.is_file() for path in files)
            else:
                for path in files:
                    _collect(_read_json(path, found.errors), scope="project", source=str(path), environ=env, cwd=cwd, into=project, errors=found.errors)
        enable_all = settings.get("enableAllProjectMcpServers") is True or any(entry.get("enableAllProjectMcpServers") is True for entry in entries)
        enabled = _names(settings.get("enabledMcpjsonServers")).union(*(_names(entry.get("enabledMcpjsonServers")) for entry in entries))
        disabled = _names(settings.get("disabledMcpjsonServers")).union(*(_names(entry.get("disabledMcpjsonServers")) for entry in entries))
        approved: dict[str, FileServer] = {}
        for name, server in project.items():
            if name in disabled:
                continue
            if enable_all or name in enabled:
                approved[name] = server
            else:
                found.pending.append(server)
        merged = {**user, **approved, **local}  # local > project > user
    slugs: dict[str, str] = {}
    for name, server in merged.items():
        if len(found.servers) >= MAX_FILE_SERVERS:
            found.errors.append((server.source, f"more than {MAX_FILE_SERVERS} servers: the rest are ignored"))
            break
        refusal = policy_refusal(name, command_line=server.command_line, url=server.url or None, settings=settings)
        if refusal is not None:
            found.errors.append((f"{server.source}: {name}", f"not loaded: {refusal}"))
            continue
        slug = slugify(name)
        if slug in slugs:
            found.errors.append((f"{server.source}: {name}", f"its tool prefix is the same as server '{slugs[slug]}'"))
            continue
        slugs[slug] = name
        found.servers.append(server)
    return found


__all__ = [
    "FileServer", "FileServerSet", "MAX_FILE_SERVERS", "McpConfigError", "expand", "load_file_servers", "parse_server",
    "policy_refusal",
]
