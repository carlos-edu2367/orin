from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .models import McpServerContribution, plugin_id_from_name

SEMVER = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")


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


def _text(value: Any, limit: int = 1024) -> str:
    return str(value or "")[:limit]


def parse_plugin_manifest(payload: Mapping[str, Any]) -> PluginManifest:
    if not isinstance(payload, Mapping):
        raise ManifestRejected("plugin.json must be an object")
    name = _text(payload.get("name"), 120).strip()
    if not name:
        raise ManifestRejected("plugin.json must declare a name")
    version = _text(payload.get("version"), 64).strip()
    if not SEMVER.fullmatch(version):
        raise ManifestRejected(f"plugin version '{version}' is not semantic versioning")
    author = payload.get("author")
    if isinstance(author, Mapping):
        author = author.get("name")
    homepage = _text(payload.get("homepage"), 512).strip() or None
    if homepage and not homepage.lower().startswith("https://"):
        homepage = None
    return PluginManifest(plugin_id_from_name(name), name, version, _text(payload.get("description")), _text(author, 120), homepage)


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
