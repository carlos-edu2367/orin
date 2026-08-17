"""Active plugin commands, and the resolution of a typed ``/token``.

Mirrors the shape of the skill library the activator already depends on:
installed per plugin, removed per plugin, and queried per user. State is
in-process because the source of truth is the plugin package on disk plus
the ``plugin_contributions`` rows; this is a lookup index, not a store.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock


@dataclass(frozen=True, slots=True)
class ResolvedCommand:
    command_id: str
    plugin_id: str
    slug: str
    path: Path


@dataclass(frozen=True, slots=True)
class _Entry:
    plugin_id: str
    slug: str
    description: str
    argument_hint: str
    path: Path


class CommandLibrary:
    def __init__(self) -> None:
        self._entries: dict[str, dict[str, _Entry]] = {}
        self._lock = RLock()

    def install_plugin_commands(self, *, user_id: str, plugin_id: str, install_path: Path, commands, commands_path: str = "commands") -> None:
        with self._lock:
            registry = self._entries.setdefault(user_id, {})
            for item in commands:
                registry[item.command_id] = _Entry(
                    plugin_id, item.slug, item.description, item.argument_hint,
                    Path(install_path) / commands_path / item.relative_path,
                )

    def remove_plugin_commands(self, *, user_id: str, plugin_id: str) -> None:
        with self._lock:
            registry = self._entries.get(user_id, {})
            for command_id in [key for key, entry in registry.items() if entry.plugin_id == plugin_id]:
                registry.pop(command_id, None)

    def list(self, user_id: str) -> tuple[dict[str, object], ...]:
        with self._lock:
            registry = dict(self._entries.get(user_id, {}))
        ambiguous = self._ambiguous(registry)
        return tuple(sorted(
            ({
                "command_id": command_id, "slug": entry.slug, "plugin_id": entry.plugin_id,
                "description": entry.description, "argument_hint": entry.argument_hint,
                "qualified": entry.slug in ambiguous,
            } for command_id, entry in registry.items()),
            key=lambda item: (str(item["slug"]), str(item["plugin_id"])),
        ))

    def resolve(self, user_id: str, token: str) -> ResolvedCommand | None:
        with self._lock:
            registry = dict(self._entries.get(user_id, {}))
        token = token.strip()
        if ":" in token:
            entry = registry.get(token)
            return ResolvedCommand(token, entry.plugin_id, entry.slug, entry.path) if entry else None
        # A bare slug is only usable while it is unambiguous. Two active
        # plugins claiming the same name never silently pick a winner.
        matches = [(key, entry) for key, entry in registry.items() if entry.slug == token]
        if len(matches) != 1:
            return None
        command_id, entry = matches[0]
        return ResolvedCommand(command_id, entry.plugin_id, entry.slug, entry.path)

    @staticmethod
    def _ambiguous(registry: dict[str, _Entry]) -> set[str]:
        seen: dict[str, int] = {}
        for entry in registry.values():
            seen[entry.slug] = seen.get(entry.slug, 0) + 1
        return {slug for slug, count in seen.items() if count > 1}
