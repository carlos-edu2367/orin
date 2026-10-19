"""A person's active plugins for one turn (W5): the agents and output styles their packages add.

Skills, MCP servers, commands and hooks are installed when a plugin is approved; agents and output styles are read
from the package each turn. ``enabledPlugins`` in the settings can turn an installed plugin off for a workspace
(``"name@marketplace": false``); one it turns on that is not installed is reported, not fetched.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import PluginManifest, manifest_file, parse_plugin_manifest
from .models import plugin_id_from_name

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ActivePlugin:
    plugin_id: str
    install_path: Path
    manifest: PluginManifest
    enabled_hook_ids: frozenset[str] = frozenset()  # consent references ("{hook_id}#{digest}") the person allowed (W5)
    enabled_lsp_ids: frozenset[str] = frozenset()  # consent references ("{name}#{digest}") of its language servers (W5-R6)

    def folders(self, relative_paths: Sequence[str]) -> list[Path]:
        return [self.install_path / item for item in relative_paths if (self.install_path / item).is_dir()]


def _setting_ids(settings: Mapping[str, Any], wanted: bool) -> dict[str, str]:
    raw = settings.get("enabledPlugins")
    if not isinstance(raw, Mapping):
        return {}
    found: dict[str, str] = {}
    for key, value in raw.items():
        if value is wanted and isinstance(key, str) and key.strip():
            try:
                found[plugin_id_from_name(key.split("@", 1)[0])] = key
            except ValueError:
                continue
    return found


def active_plugins(plugin_service: Any, user_id: str, settings: Mapping[str, Any]) -> tuple[list[ActivePlugin], list[str]]:
    """``(the active plugins this turn, the enabledPlugins entries that name a plugin not installed)``."""
    if plugin_service is None or not user_id:
        return [], []
    try:
        rows = list(plugin_service.list(user_id))
    except Exception:  # noqa: BLE001 - plugins never stop a turn
        logger.exception("the person's plugins could not be listed")
        return [], []
    turned_off = _setting_ids(settings, False)
    installed = {str(row.get("plugin_id") or "") for row in rows}
    missing = [key for plugin_id, key in _setting_ids(settings, True).items() if plugin_id not in installed]
    active: list[ActivePlugin] = []
    for row in rows:
        plugin_id = str(row.get("plugin_id") or "")
        if row.get("state") != "active" or plugin_id in turned_off:
            continue
        path = Path(str(row.get("install_path") or ""))
        target = manifest_file(path)
        if target is None:
            continue
        try:
            manifest = parse_plugin_manifest(json.loads(target.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            logger.warning("plugin %s has an unreadable manifest; its agents and styles are skipped", plugin_id)
            continue
        allowed = frozenset(
            str(item.get("reference") or "") for item in row.get("contributions") or ()
            if isinstance(item, Mapping) and item.get("kind") == "hook" and item.get("enabled")
        )
        allowed_lsp = frozenset(
            str(item.get("reference") or "") for item in row.get("contributions") or ()
            if isinstance(item, Mapping) and item.get("kind") == "lsp" and item.get("enabled")
        )
        active.append(ActivePlugin(plugin_id, path, manifest, allowed, enabled_lsp_ids=allowed_lsp))
    return active, sorted(missing)


__all__ = ["ActivePlugin", "active_plugins"]
