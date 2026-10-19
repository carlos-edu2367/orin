"""Rebuild a process-local plugin index from the plugins already installed.

The command library and hook engine are in-process lookup indexes, and the
API and the chat worker are separate processes. Each rebuilds its own index
at startup (or before a turn) from the packages on disk and the persisted
contribution rows, which stay the single source of truth.
"""
from __future__ import annotations

from pathlib import Path

from .command_library import CommandLibrary
from .hooks_manifest import consented_hooks
from .inspector import inspect_plugin_package
from .models import PluginState


def rehydrate_commands(plugin_service, command_library: CommandLibrary, *, user_id: str) -> None:
    for record in plugin_service.list(user_id):
        if str(record.get("state")) != PluginState.ACTIVE.value:
            continue
        try:
            inspection = inspect_plugin_package(
                Path(str(record["install_path"])), package_digest=str(record["package_digest"])
            )
        except Exception:  # noqa: BLE001 - one broken package never blocks the rest
            continue
        command_library.install_plugin_commands(
            user_id=user_id, plugin_id=inspection.ref.plugin_id,
            install_path=Path(str(record["install_path"])), commands=inspection.commands,
        )


def rehydrate_hooks(plugin_service, hook_engine, *, user_id: str) -> None:
    for record in plugin_service.list(user_id):
        if str(record.get("state")) != PluginState.ACTIVE.value:
            continue
        plugin_id = str(record["plugin_id"])
        try:
            inspection = inspect_plugin_package(
                Path(str(record["install_path"])), package_digest=str(record["package_digest"])
            )
        except Exception:  # noqa: BLE001 - one broken package never blocks the rest
            continue
        if not inspection.hooks:
            continue
        contributions = plugin_service._contributions(user_id, plugin_id)
        # W5: a hook runs only while its own row (its id and digest) is enabled; a row written before the digest
        # was part of the reference matches nothing, so it never counts as consent.
        allowed = [str(item.get("reference") or "") for item in contributions if item.get("kind") == "hook" and item.get("enabled")]
        hook_engine.register(
            user_id=user_id, plugin_id=plugin_id, install_path=Path(str(record["install_path"])),
            hooks=consented_hooks(inspection.hooks, allowed), enabled=True,
        )
