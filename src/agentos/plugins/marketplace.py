from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


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


def parse_marketplace(payload: Mapping[str, Any]) -> Marketplace:
    entries = payload.get("plugins") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list) or not entries:
        raise MarketplaceRejected("marketplace must declare plugins")
    plugins: list[MarketplacePlugin] = []
    for item in entries:
        if not isinstance(item, Mapping) or not item.get("name"):
            continue
        source = item.get("source")
        if isinstance(source, str):
            reference = source
        elif isinstance(source, Mapping) and source.get("repo"):
            reference = str(source["repo"])
        else:
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
