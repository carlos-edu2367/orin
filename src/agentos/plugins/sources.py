from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ipaddress
import re
from urllib.parse import urlparse


class SourceRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PluginSource:
    kind: str
    url: str | None = None
    ref: str | None = None
    subdirectory: str | None = None
    path: str | None = None
    plugin_name: str | None = None
    suggested_name: str = ""


def _public_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
        raise SourceRejected("only public HTTPS plugin sources are supported")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        raise SourceRejected("plugin source host is not public")


def resolve_source(reference: str) -> PluginSource:
    value = str(reference).strip()
    if not value:
        raise SourceRejected("plugin reference is required")
    local = Path(value)
    if local.exists():
        if not local.is_dir():
            raise SourceRejected("local plugin source must be a directory")
        return PluginSource("path", path=str(local.resolve()), suggested_name=local.name)
    if value.startswith("https://"):
        parsed = urlparse(value)
        _public_url(value)
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.netloc.lower() != "github.com" or len(parts) < 2:
            raise SourceRejected("plugin URL must point to a public GitHub repository")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        ref = None
        subdirectory = None
        if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
            ref, subdirectory = parts[3], "/".join(parts[4:]) or None
        return PluginSource("git", f"https://github.com/{owner}/{repo}.git", ref, subdirectory, suggested_name=repo)
    if "://" in value or value.startswith("git@"):
        raise SourceRejected("only HTTPS, GitHub shorthand, marketplace names, or local paths are supported")
    if re.fullmatch(r"[\w.-]+/[\w.-]+", value):
        owner, repo = value.split("/", 1)
        return PluginSource("git", f"https://github.com/{owner}/{repo.removesuffix('.git')}.git", suggested_name=repo.removesuffix(".git"))
    return PluginSource("marketplace", plugin_name=value, suggested_name=value)
