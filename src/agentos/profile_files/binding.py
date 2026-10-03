from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

from agentos.installation import orin_paths
from agentos.local_workspace import FolderRejected
from agentos.local_workspace.paths import FolderInspection, _count_entries

_DRIVE = re.compile(r"^[A-Za-z]:")


class ProfileFolderRejected(FolderRejected):
    """A path that is not inside the profile's own file area."""


def files_root(user_id: str) -> Path:
    root = orin_paths().user_files(user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_inside(root: Path, relative: str) -> Path:
    raw = (relative or "").strip().replace("\\", "/")
    if "\0" in raw:
        raise ProfileFolderRejected("path is not valid")
    if raw.startswith("/") or _DRIVE.match(raw):
        raise ProfileFolderRejected("path must be relative to the profile area")
    parts = [part for part in PurePosixPath(raw).parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ProfileFolderRejected("path must not leave the profile area")
    try:
        base = Path(root).resolve()
        target = base.joinpath(*parts).resolve()
    except (OSError, ValueError) as error:
        # A NUL byte or an over-long component is a bad request, not a crash.
        raise ProfileFolderRejected("path is not valid") from error
    if target != base and not target.is_relative_to(base):
        raise ProfileFolderRejected("path resolves outside the profile area")
    return target


def relative_display(root: Path, absolute: str | Path) -> str | None:
    base = Path(root).resolve()
    try:
        target = Path(absolute).resolve()
    except (OSError, ValueError):
        return None
    if target == base:
        return "."
    if not target.is_relative_to(base):
        return None
    return target.relative_to(base).as_posix()


def inspect_profile_folder(root: Path, relative: str) -> FolderInspection:
    target = resolve_inside(root, relative)
    is_directory = target.is_dir()
    count, truncated = _count_entries(target) if is_directory else (0, False)
    display = relative_display(root, target) or "."
    return FolderInspection(display, target.exists(), is_directory, is_directory and os.access(target, os.W_OK), count, truncated, "none")


__all__ = ["ProfileFolderRejected", "files_root", "inspect_profile_folder", "relative_display", "resolve_inside"]
