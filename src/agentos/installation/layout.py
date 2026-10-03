"""Move pre-profile data into the per-profile layout, once.

Before profiles existed every managed workspace lived in ``<data>/workspaces``
and belonged to the single loopback principal, ``local-user``. Each entry is
moved with a rename, so a crash leaves every entry either fully on the old
side or fully on the new one and the next boot resumes.
"""
from __future__ import annotations

import os
import shutil

from .paths import OrinPaths

LAYOUT_MARKER = ".layout-v2"
_LEGACY_OWNER = "local-user"


class LayoutMigrationError(RuntimeError):
    pass


def migrate_data_layout(paths: OrinPaths) -> bool:
    marker = paths.data / LAYOUT_MARKER
    if marker.is_file():
        return False
    legacy = paths.workspaces
    target = paths.user_workspaces(_LEGACY_OWNER)
    moved = False
    if legacy.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for entry in sorted(legacy.iterdir()):
            destination = target / entry.name
            if destination.exists():
                raise LayoutMigrationError(f"{entry.name} exists in both {legacy} and {target}; resolve it by hand and start again")
            try:
                os.replace(entry, destination)
            except OSError:
                shutil.move(str(entry), str(destination))
            moved = True
        legacy.rmdir()
    paths.data.mkdir(parents=True, exist_ok=True)
    marker.write_text("users/<user_id>/workspaces\n", encoding="utf-8")
    return moved


__all__ = ["LAYOUT_MARKER", "LayoutMigrationError", "migrate_data_layout"]
