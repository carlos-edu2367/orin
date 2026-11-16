"""Validation and normalization of user-attached workspace images for `codex exec --image`."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from typing import Sequence

ALLOWED_CODEX_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
    }
)
DEFAULT_MAX_CODEX_IMAGES: int = 8
DEFAULT_MAX_CODEX_IMAGE_BYTES: int = 20 * 1024 * 1024

_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


def _has_control_chars(value: str) -> bool:
    return any(
        ord(ch) < 32 or ord(ch) == 127 or ch in {"\x85", "\u2028", "\u2029"}
        for ch in value
    )


def validated_codex_images(
    paths: Sequence[str | Path],
    workspace_root: Path,
    *,
    max_images: int = DEFAULT_MAX_CODEX_IMAGES,
    max_bytes: int = DEFAULT_MAX_CODEX_IMAGE_BYTES,
) -> tuple[Path, ...]:
    """Validate, resolve, deduplicate, and bound workspace image paths for `codex exec --image`.

    Invalid entries are silently skipped so the turn never fails to start; the attachment remains
    referenced in the message text and accessible via Orin's `ViewFile` tool.
    """
    if (
        not paths
        or isinstance(paths, (str, bytes, bytearray))
        or max_images <= 0
        or max_bytes <= 0
        or not isinstance(workspace_root, (str, Path))
    ):
        return ()

    root_raw = str(workspace_root).strip()
    if (
        not root_raw
        or _has_control_chars(root_raw)
        or root_raw.startswith(("\\\\", "//"))
    ):
        return ()

    try:
        resolved_root = Path(workspace_root).resolve(strict=True)
        if not resolved_root.is_dir():
            return ()
    except (OSError, ValueError, RuntimeError):
        return ()

    accepted: list[Path] = []
    seen: set[str] = set()

    for raw_item in paths:
        if len(accepted) >= max_images:
            break
        if not isinstance(raw_item, (str, Path)):
            continue
        raw_str = str(raw_item)
        if _has_control_chars(raw_str):
            continue
        text = raw_str.strip()
        # Reject empty paths, option-like flags starting with '-', and any path containing ','
        # because Codex's clap parser sets `value_delimiter = ','` on `--image`.
        if not text or text.startswith("-") or "," in text:
            continue
        if text.startswith(("\\\\", "//")):
            continue

        after_drive = text[2:] if _DRIVE_PREFIX_RE.match(text) else text
        if ":" in after_drive:
            continue

        raw_segments = [
            seg for seg in re.split(r"[\\/]+", after_drive) if seg != ""
        ]
        if not raw_segments:
            continue
        if any(
            seg in (".", "..")
            or seg.startswith("-")
            or seg.endswith((" ", "."))
            or "," in seg
            for seg in raw_segments
        ):
            continue

        try:
            candidate = Path(text)
            if not candidate.is_absolute():
                candidate = Path(workspace_root) / candidate
            resolved = candidate.resolve(strict=True)
        except (OSError, ValueError, RuntimeError):
            continue

        if (
            resolved == resolved_root
            or not resolved.is_relative_to(resolved_root)
        ):
            continue

        try:
            rel = resolved.relative_to(resolved_root)
        except ValueError:
            continue

        if any(
            part in ("", ".", "..") or part.startswith("-") or "," in part
            for part in rel.parts
        ):
            continue

        resolved_str = str(resolved)
        if (
            _has_control_chars(resolved_str)
            or resolved_str.startswith("-")
            or "," in resolved_str
        ):
            continue

        if resolved.suffix.lower() not in ALLOWED_CODEX_IMAGE_EXTENSIONS:
            continue

        try:
            st = resolved.stat()
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        if st.st_size <= 0 or st.st_size > max_bytes:
            continue

        dedupe_key = os.path.normcase(resolved_str)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        accepted.append(resolved)

    return tuple(accepted)


__all__ = [
    "ALLOWED_CODEX_IMAGE_EXTENSIONS",
    "DEFAULT_MAX_CODEX_IMAGES",
    "DEFAULT_MAX_CODEX_IMAGE_BYTES",
    "validated_codex_images",
]
