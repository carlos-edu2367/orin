from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

from .models import McpServerContribution, plugin_id_from_name

SEMVER = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")
# W5: where a package keeps its manifest, Orin's own folder first; a version is optional, as in Claude Code.
MANIFEST_LOCATIONS = (".orin-plugin/plugin.json", ".claude-plugin/plugin.json", "plugin.json")
DEFAULT_VERSION = "0.0.0"
MAX_COMPONENT_PATHS = 10


def manifest_file(root: Path) -> Path | None:
    for relative in MANIFEST_LOCATIONS:
        candidate = Path(root) / relative
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


class ManifestRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PluginManifest:
    plugin_id: str
    display_name: str
    version: str
    description: str
    author: str
    homepage: str | None
    mcp_servers: tuple[McpServerContribution, ...] = ()
    commands_path: str = "commands"
    hooks_path: str = "hooks"
    agents_paths: tuple[str, ...] = ("agents",)
    skills_paths: tuple[str, ...] = ("skills",)
    output_styles_paths: tuple[str, ...] = ("output-styles",)
    mcp_config_path: str = ".mcp.json"


def _text(value: Any, limit: int = 1024) -> str:
    return str(value or "")[:limit]


def _package_relative_path(value: Any, default: str) -> str:
    """A manifest-declared subdirectory, guaranteed to stay inside the package.

    Validation is textual and happens before any filesystem access, so a
    hostile manifest is refused without the package being touched. Symlink
    escapes are caught later, at read time, by resolving against the root.
    """
    raw = _text(value, 512).strip().replace("\\", "/")
    if not raw:
        return default
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or (len(raw) > 1 and raw[1] == ":"):
        raise ManifestRejected(f"path '{raw}' must be relative to the plugin package")
    parts = [part for part in candidate.parts if part not in (".",)]
    if any(part == ".." for part in parts):
        raise ManifestRejected(f"path '{raw}' must not escape the plugin package")
    return "/".join(parts) or default


def _package_paths(value: Any, default: str) -> tuple[str, ...]:
    """One path or a list of them (Claude Code accepts both), each kept inside the package."""
    if value is None:
        return (default,)
    items = value if isinstance(value, list) else [value]
    paths = tuple(_package_relative_path(item, default) for item in items[:MAX_COMPONENT_PATHS] if isinstance(item, str))
    return tuple(dict.fromkeys(paths)) or (default,)


def _first_path(value: Any) -> Any:
    return (value[0] if value else None) if isinstance(value, list) else value


def parse_plugin_manifest(payload: Mapping[str, Any]) -> PluginManifest:
    if not isinstance(payload, Mapping):
        raise ManifestRejected("plugin.json must be an object")
    name = _text(payload.get("name"), 120).strip()
    if not name:
        raise ManifestRejected("plugin.json must declare a name")
    version = _text(payload.get("version"), 64).strip() or DEFAULT_VERSION
    if not SEMVER.fullmatch(version):
        raise ManifestRejected(f"plugin version '{version}' is not semantic versioning")
    author = payload.get("author")
    if isinstance(author, Mapping):
        author = author.get("name")
    homepage = _text(payload.get("homepage"), 512).strip() or None
    if homepage and not homepage.lower().startswith("https://"):
        homepage = None
    return PluginManifest(
        plugin_id_from_name(name), name, version, _text(payload.get("description")),
        _text(author, 120), homepage, parse_mcp_config(payload),
        _package_relative_path(_first_path(payload.get("commands")), "commands"),
        _package_relative_path(payload.get("hooks") if isinstance(payload.get("hooks"), str) else None, "hooks"),
        agents_paths=_package_paths(payload.get("agents"), "agents"),
        skills_paths=_package_paths(payload.get("skills"), "skills"),
        output_styles_paths=_package_paths(payload.get("outputStyles"), "output-styles"),
        mcp_config_path=_package_relative_path(payload.get("mcpServers") if isinstance(payload.get("mcpServers"), str) else None, ".mcp.json"),
    )


def parse_mcp_config(payload: Mapping[str, Any]) -> tuple[McpServerContribution, ...]:
    servers = payload.get("mcpServers") if isinstance(payload, Mapping) else None
    if not isinstance(servers, Mapping):
        return ()
    result: list[McpServerContribution] = []
    for raw_slug, raw in list(servers.items())[:16]:
        if not isinstance(raw, Mapping):
            continue
        try:
            slug = plugin_id_from_name(str(raw_slug))[:32]
        except ValueError:
            continue
        url = _text(raw.get("url"), 2048).strip() or None
        command = _text(raw.get("command"), 512).strip() or None
        declared = _text(raw.get("type"), 16).strip().lower()
        transport = declared if declared in {"stdio", "http"} else ("http" if url else "stdio")
        if (transport == "http" and url and not url.lower().startswith("https://")):
            continue
        if (transport == "stdio" and not command) or (transport == "http" and not url):
            continue
        env = raw.get("env") if isinstance(raw.get("env"), Mapping) else {}
        args = raw.get("args") if isinstance(raw.get("args"), list) else []
        result.append(McpServerContribution(
            slug, _text(raw.get("displayName") or raw_slug, 120), transport,
            command if transport == "stdio" else None,
            tuple(_text(item, 512) for item in args if isinstance(item, str)),
            url if transport == "http" else None,
            tuple(str(key)[:120] for key in env),
        ))
    return tuple(result)
