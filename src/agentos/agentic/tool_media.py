"""Images a tool returned, saved in the workspace so a CLI that drops them can still be pointed at the file.

agy 1.5.6 hands the model an MCP tool's image block only up to ~10 KB; a larger one is written under agy's own
store and the model gets only that path. Orin therefore also saves each image under
``<workspace>/.orin/tool-media/<call_id>-<n>.<ext>`` and names the absolute path in the tool result, so the model
opens it with its native ``read_file`` (the media gate allows exactly this folder, being inside the workspace).
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import re
import time

TOOL_MEDIA_DIRECTORY = ".orin/tool-media"
# Stale images are removed when the next one is saved; the tool result of that turn already carried the file.
TOOL_MEDIA_TTL_SECONDS = 24 * 60 * 60
MAX_TOOL_IMAGE_BYTES = 50 * 1024 * 1024

_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_call_id(call_id: str) -> str:
    cleaned = _UNSAFE.sub("_", str(call_id or "")).strip("._-")[:80]
    return cleaned or "call"


def purge_tool_media(root: Path | str, *, ttl_seconds: float = TOOL_MEDIA_TTL_SECONDS, now: float | None = None) -> int:
    """Delete the saved tool images older than ``ttl_seconds``. Returns how many were removed."""
    directory = Path(root) / TOOL_MEDIA_DIRECTORY
    if not directory.is_dir() or directory.is_symlink():
        return 0
    cutoff = (time.time() if now is None else now) - ttl_seconds
    removed = 0
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def save_tool_images(
    root: Path | str,
    call_id: str,
    images: Sequence[Mapping[str, str]],
) -> list[Path]:
    """Write each ``{"data": base64, "mime_type": ...}`` image under the workspace; returns the absolute paths.

    An image that cannot be decoded, is too large or is not a PNG/JPEG/WebP/GIF is skipped: the inline block
    still goes to the model, this only adds the file.
    """
    base = Path(root)
    directory = base / TOOL_MEDIA_DIRECTORY
    saved: list[Path] = []
    prepared: list[tuple[bytes, str]] = []
    for item in images:
        if not isinstance(item, Mapping):
            continue
        extension = _EXTENSIONS.get(str(item.get("mime_type") or "image/png").strip().lower())
        if extension is None:
            continue
        try:
            data = base64.b64decode(str(item.get("data") or ""), validate=False)
        except (binascii.Error, ValueError):
            continue
        if not data or len(data) > MAX_TOOL_IMAGE_BYTES:
            continue
        prepared.append((data, extension))
    if not prepared:
        return saved
    try:
        from agentos.agentic.git_exclude import ensure_orin_excluded

        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            return saved
        try:
            ensure_orin_excluded(base)
        except Exception:  # noqa: BLE001 - keeping `.orin` out of git never blocks the image
            pass
        purge_tool_media(base)
        stem = _safe_call_id(call_id)
        for index, (data, extension) in enumerate(prepared):
            target = directory / f"{stem}-{index}{extension}"
            if target.is_symlink():
                continue
            tmp = target.with_name(f"{target.name}.part")
            tmp.write_bytes(data)
            os.replace(tmp, target)
            saved.append(target.resolve())
    except OSError:
        return saved
    return saved


def tool_media_note(paths: Sequence[Path]) -> str:
    """The sentence appended to a tool result: where the image is and how to see it."""
    if not paths:
        return ""
    if len(paths) == 1:
        return (
            f"Imagem salva em {paths[0].as_posix()}. "
            "Para ver, chame sua ferramenta nativa read_file com esse caminho."
        )
    listed = ", ".join(path.as_posix() for path in paths)
    return (
        f"Imagens salvas em {listed}. "
        "Para ver, chame sua ferramenta nativa read_file com cada um desses caminhos."
    )


__all__ = [
    "MAX_TOOL_IMAGE_BYTES",
    "TOOL_MEDIA_DIRECTORY",
    "TOOL_MEDIA_TTL_SECONDS",
    "purge_tool_media",
    "save_tool_images",
    "tool_media_note",
]
