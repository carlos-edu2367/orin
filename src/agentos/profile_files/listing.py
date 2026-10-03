from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .binding import ProfileFolderRejected, relative_display, resolve_inside

MAX_ENTRIES = 1000


def list_folder(root: Path, relative: str) -> dict[str, object]:
    target = resolve_inside(root, relative)
    if not target.is_dir():
        raise ProfileFolderRejected("folder does not exist")
    directories, files = [], []
    for item in target.iterdir():
        if item.is_symlink():
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        entry = {"name": item.name, "kind": "directory" if item.is_dir() else "file", "bytes": 0 if item.is_dir() else stat.st_size,
                 "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()}
        (directories if item.is_dir() else files).append(entry)
    entries = sorted(directories, key=lambda e: e["name"]) + sorted(files, key=lambda e: e["name"])
    return {"path": relative_display(root, target) or ".", "entries": entries[:MAX_ENTRIES], "truncated": len(entries) > MAX_ENTRIES}


def make_folder(root: Path, parent: str, name: str) -> str:
    clean = (name or "").strip()
    if clean in ("", ".", "..") or "/" in clean or "\\" in clean or len(clean) > 255:
        raise ProfileFolderRejected("folder name is not valid")
    base = resolve_inside(root, parent)
    if not base.is_dir():
        raise ProfileFolderRejected("parent folder does not exist")
    created = resolve_inside(root, f"{relative_display(root, base)}/{clean}")
    try:
        created.mkdir(exist_ok=True)
    except OSError as error:
        raise ProfileFolderRejected("folder could not be created") from error
    return relative_display(root, created) or "."


__all__ = ["MAX_ENTRIES", "list_folder", "make_folder"]
