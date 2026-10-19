from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

# W5: where a marketplace keeps its index, Orin's own folder first (Claude Code: .claude-plugin/marketplace.json).
MARKETPLACE_LOCATIONS = (".orin-plugin/marketplace.json", ".claude-plugin/marketplace.json", "marketplace.json")
_GITHUB_REPO = re.compile(r"[\w.-]+/[\w.-]+")


def marketplace_file(root: Path) -> Path | None:
    for relative in MARKETPLACE_LOCATIONS:
        candidate = Path(root) / relative
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def _github(repository: str, ref: Any = None, path: Any = None) -> str | None:
    """A GitHub source as a reference the plugin installer understands (``owner/repo`` or a ``/tree/`` URL)."""
    text = str(repository or "").strip()
    base = text.removeprefix("https://github.com/").removesuffix(".git").strip("/")
    if not _GITHUB_REPO.fullmatch(base):
        return None
    folder = str(path or "").strip().strip("/")
    if ".." in folder.split("/"):
        return None
    if not folder and not ref:
        return text if text.startswith("https://") else base
    return f"https://github.com/{base}/tree/{str(ref or 'HEAD').strip()}/{folder}".rstrip("/")


def _reference(source: Any, root: Path | None) -> str | None:
    if isinstance(source, str):
        text = source.strip()
        if text.startswith(("./", "../")):
            if root is None:
                return None
            target = (Path(root) / text).resolve()
            return str(target) if target.is_relative_to(Path(root).resolve()) and target.is_dir() else None
        return text or None
    if not isinstance(source, Mapping):
        return None
    kind = str(source.get("source") or "").strip().lower()
    if kind == "github" or (not kind and source.get("repo")):
        return _github(str(source.get("repo") or ""), source.get("ref"), source.get("path"))
    if kind == "url":
        url = str(source.get("url") or "").strip()
        return _github(url, source.get("ref"), None) if source.get("ref") else (url or None)
    if kind == "git-subdir":
        return _github(str(source.get("url") or ""), source.get("ref"), source.get("path"))
    return None


class MarketplaceRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MarketplacePlugin:
    name: str
    reference: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class Marketplace:
    name: str
    owner: str
    plugins: tuple[MarketplacePlugin, ...]


def parse_marketplace(payload: Mapping[str, Any], *, root: Path | None = None) -> Marketplace:
    """The marketplace's plugins with installable references. ``root`` is the marketplace's folder: a relative
    source (``./plugins/x``) becomes a path inside it, and one that leaves it is dropped."""
    entries = payload.get("plugins") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list) or not entries:
        raise MarketplaceRejected("marketplace must declare plugins")
    plugins: list[MarketplacePlugin] = []
    for item in entries:
        if not isinstance(item, Mapping) or not item.get("name"):
            continue
        reference = _reference(item.get("source"), root)
        if reference is None:
            continue
        plugins.append(MarketplacePlugin(str(item["name"]), reference, str(item.get("description") or "")))
    if not plugins:
        raise MarketplaceRejected("marketplace has no valid plugins")
    owner = payload.get("owner")
    owner = owner.get("name") if isinstance(owner, Mapping) else owner
    return Marketplace(str(payload.get("name") or "marketplace"), str(owner or ""), tuple(plugins))


def find_plugin_entry(marketplace: Marketplace, name: str) -> MarketplacePlugin | None:
    return next((item for item in marketplace.plugins if item.name.casefold() == str(name).casefold()), None)


DEFAULT_MARKETPLACES = ("https://github.com/obra/superpowers.git",)
