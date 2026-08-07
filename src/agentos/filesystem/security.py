from __future__ import annotations

import unicodedata

from .models import WorkspacePath


def validate_path_text(value: str) -> WorkspacePath:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path must be non-empty")
    if any(marker in value for marker in ("\\", "//", ":", "?", "\x00")):
        raise ValueError("path contains a reserved marker")
    if value.startswith(("/", "~", "$", "%")) or value.startswith(("http", "file")):
        raise ValueError("path is not a logical WorkspacePath")
    if any(unicodedata.normalize("NFC", part) != part for part in value.split("/")):
        raise ValueError("path is not canonically normalized")
    return WorkspacePath.from_string(value)


def reject_empty_destructive_path(path: WorkspacePath) -> None:
    if not path.segments:
        raise ValueError("destructive operations cannot target the Workspace root")


__all__ = ["reject_empty_destructive_path", "validate_path_text"]
