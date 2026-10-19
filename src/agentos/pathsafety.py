"""Shared sandbox-containment guard for scanning below a trusted root.

A directory-shaped symlink planted inside a sandbox can point outside it;
resolving the candidate and re-checking containment against the root is what
actually rules that out, since ``resolve()`` follows the link. Anything that
walks a filesystem tree it does not fully trust — the conversation workspace,
the retrieval indexer — uses this same check, so a future fix (a Windows
dangling-symlink edge case, say) only has one place to land.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def resolve_contained(item: Path, root: Path) -> Path | None:
    """Resolve ``item`` and return it only if it stays inside ``root``.

    Returns ``None`` on any resolution failure (a dangling symlink, a
    permission error) or when the resolved path escaped ``root`` — the two
    failure modes every caller already treats identically.
    """
    try:
        resolved = item.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def is_drive_path(raw: str) -> bool:
    """``C:\\x`` or ``C:/x``: an absolute Windows path, on every host."""
    return bool(_DRIVE.match(str(raw).strip()))


def _components(value: str) -> list[str]:
    return [part for part in re.split(r"[\\/]+", value) if part not in ("", ".")]


def absolute_inside(raw: str, root: Path) -> str | None:
    """The root-relative POSIX path of an absolute ``raw`` that lies under ``root``; ``""`` for the root itself.

    Lexical only: nothing is opened, so a network path is never touched here, and a link is
    still caught by the caller's containment check after resolution. A drive path is absolute
    on every host and compared case-insensitively; a leading ``/`` is absolute only on POSIX.
    Any ``..`` makes the answer ``None``.
    """
    text = str(raw).strip()
    drive = is_drive_path(text)
    if not drive and not (os.name != "nt" and text.startswith("/") and not text.startswith("//")):
        return None
    parts = _components(text)
    if ".." in parts:
        return None
    fold = drive or os.name == "nt"
    bases = [str(root)]
    try:
        bases.append(str(root.resolve()))
    except OSError:
        pass
    for base in bases:
        base_parts = _components(base)
        head = parts[: len(base_parts)]
        if len(head) < len(base_parts):
            continue
        if ([item.lower() for item in head] == [item.lower() for item in base_parts]) if fold else head == base_parts:
            return "/".join(parts[len(base_parts):])
    return None


__all__ = ["absolute_inside", "is_drive_path", "resolve_contained"]
