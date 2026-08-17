"""Rebuild a process-local command index from the plugins already installed.

The command library is an in-process lookup index, and the API and the chat
worker are separate processes. Each rebuilds its own index at startup from the
packages on disk, which stay the single source of truth.
"""
from __future__ import annotations

from pathlib import Path

from .command_library import CommandLibrary
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
