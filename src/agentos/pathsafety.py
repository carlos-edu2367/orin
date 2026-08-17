"""Shared sandbox-containment guard for scanning below a trusted root.

A directory-shaped symlink planted inside a sandbox can point outside it;
resolving the candidate and re-checking containment against the root is what
actually rules that out, since ``resolve()`` follows the link. Anything that
walks a filesystem tree it does not fully trust — the conversation workspace,
the retrieval indexer — uses this same check, so a future fix (a Windows
dangling-symlink edge case, say) only has one place to land.
"""
from __future__ import annotations

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


__all__ = ["resolve_contained"]
