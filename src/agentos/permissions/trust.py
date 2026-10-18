"""Which workspaces a user trusts to run repository-provided configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path

from agentos.settings.paths import orin_user_dir
from agentos.settings.writer import SettingsWriteError, update_settings_file


def _key(root: Path) -> str:
    return os.path.normcase(str(Path(root).resolve()))


class WorkspaceTrustStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else orin_user_dir() / "trusted-workspaces.json"

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def is_trusted(self, user_id: str, root: Path) -> bool:
        users = self._read().get("users")
        if not isinstance(users, dict):
            return False
        entries = users.get(str(user_id))
        return isinstance(entries, list) and _key(root) in entries

    def _mutate(self, user_id: str, root: Path, *, add: bool) -> None:
        key = _key(root)

        def change(data: dict) -> None:
            data["version"] = 1
            users = data.get("users")
            if not isinstance(users, dict):
                users = {}
                data["users"] = users
            entries = users.get(str(user_id))
            if not isinstance(entries, list):
                entries = []
            entries = [item for item in entries if isinstance(item, str) and item != key]
            if add:
                entries.append(key)
            users[str(user_id)] = entries

        try:
            update_settings_file(self.path, change)
        except SettingsWriteError:
            if not add:
                raise
            # a corrupt file is replaced rather than leaving the user unable to trust
            self.path.unlink(missing_ok=True)
            update_settings_file(self.path, change)

    def trust(self, user_id: str, root: Path) -> None:
        self._mutate(user_id, root, add=True)

    def revoke(self, user_id: str, root: Path) -> None:
        self._mutate(user_id, root, add=False)
