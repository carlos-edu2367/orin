"""Move staged uploads into the conversation's own workspace.

Promotion happens before the turn row exists, because the publisher can hand
the turn to a worker within milliseconds of it being created and the worker has
to find the file on disk. If turn creation then fails, ``discard_promoted``
removes exactly what this call moved.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from agentos.agentic.workspace import ConversationWorkspace

from .media import MAX_FILES_PER_MESSAGE, MAX_TURN_BYTES, UploadRejected
from .staging import UploadStaging

UPLOAD_DIRECTORY = "uploads"


def _available_name(directory, filename: str) -> str:
    stem, dot, extension = filename.rpartition(".")
    if not dot:
        stem, extension = filename, ""
    suffix = f".{extension}" if extension else ""
    candidate = filename
    index = 1
    while (directory / candidate).exists():
        index += 1
        candidate = f"{stem} ({index}){suffix}"
    return candidate


def promote_uploads(
    staging: UploadStaging,
    workspace: ConversationWorkspace,
    user_id: str,
    upload_ids: Iterable[str],
    *,
    max_files: int = MAX_FILES_PER_MESSAGE,
    max_total_bytes: int = MAX_TURN_BYTES,
) -> list[dict[str, Any]]:
    identifiers = [str(item) for item in upload_ids]
    if len(identifiers) > max_files:
        raise UploadRejected("too many files for one message")
    staged = [staging.get(user_id, item) for item in identifiers]
    if sum(item.bytes for item in staged) > max_total_bytes:
        raise UploadRejected("attachments exceed the per-turn budget")
    directory = workspace.root / UPLOAD_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    try:
        for item in staged:
            name = _available_name(directory, item.filename)
            (directory / name).write_bytes(item.path.read_bytes())
            records.append({
                "path": f"{UPLOAD_DIRECTORY}/{name}", "original_name": item.filename,
                "media_type": item.media_type, "kind": item.kind, "bytes": item.bytes,
            })
    except OSError:
        discard_promoted(workspace, records)
        raise
    for item in staged:
        staging.discard(user_id, item.upload_id)
    return records


def discard_promoted(workspace: ConversationWorkspace, records: Sequence[dict[str, Any]]) -> None:
    """Undo a promotion whose turn was never created."""
    for record in records:
        try:
            target = workspace.resolve(str(record.get("path") or ""))
        except Exception:
            continue
        try:
            target.unlink(missing_ok=True)
        except OSError:
            continue


__all__ = ["UPLOAD_DIRECTORY", "discard_promoted", "promote_uploads"]
