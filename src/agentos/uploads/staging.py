"""Holding area for a file the person attached but has not sent yet.

The first message of a conversation has no conversation to write into, and the
person must be able to drop an attachment before deciding to send it. Both are
why the upload lands here first and is promoted only when the turn is created.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

from .media import MAX_UPLOAD_BYTES, UploadRejected, classify, safe_filename

_UPLOAD_ID = re.compile(r"^upl_[0-9a-f]{32}$")
_OWNER = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True, slots=True)
class StagedUpload:
    upload_id: str
    filename: str
    media_type: str
    kind: str
    bytes: int
    path: Path


class UploadStaging:
    def __init__(self, root: Path | str, *, max_bytes: int = MAX_UPLOAD_BYTES, clock=None) -> None:
        self._root = Path(root)
        self._max_bytes = int(max_bytes)
        self._clock = clock or (lambda: datetime.now(UTC))

    def _owner_directory(self, user_id: str) -> Path:
        owner = _OWNER.sub("_", str(user_id))[:64] or "anonymous"
        return self._root / owner

    def store(self, user_id: str, filename: str, data: bytes) -> StagedUpload:
        if len(data) > self._max_bytes:
            raise UploadRejected("file exceeds the upload limit")
        if not data:
            raise UploadRejected("file is empty")
        name = safe_filename(filename)
        media_type, kind = classify(name, data)
        upload_id = f"upl_{uuid4().hex}"
        directory = self._owner_directory(user_id) / upload_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        target.write_bytes(data)
        # ``purge`` decides staleness from this directory's mtime; stamping it
        # from the injected clock (rather than leaving the real filesystem
        # time) is what makes staleness deterministic and testable.
        stamp = self._clock().timestamp()
        os.utime(directory, (stamp, stamp))
        return StagedUpload(upload_id, name, media_type, kind, len(data), target)

    def get(self, user_id: str, upload_id: str) -> StagedUpload:
        if not _UPLOAD_ID.fullmatch(str(upload_id)):
            raise LookupError(upload_id)
        directory = self._owner_directory(user_id) / upload_id
        files = sorted(item for item in directory.glob("*") if item.is_file()) if directory.is_dir() else []
        if not files:
            raise LookupError(upload_id)
        target = files[0]
        data = target.read_bytes()
        media_type, kind = classify(target.name, data)
        return StagedUpload(upload_id, target.name, media_type, kind, len(data), target)

    def discard(self, user_id: str, upload_id: str) -> bool:
        if not _UPLOAD_ID.fullmatch(str(upload_id)):
            return False
        directory = self._owner_directory(user_id) / upload_id
        if not directory.is_dir():
            return False
        shutil.rmtree(directory, ignore_errors=True)
        return True

    def purge(self, *, older_than: timedelta = timedelta(hours=24)) -> int:
        """Delete staged uploads nobody sent. Returns how many were removed."""
        if not self._root.is_dir():
            return 0
        cutoff = self._clock() - older_than
        removed = 0
        for owner in self._root.iterdir():
            if not owner.is_dir():
                continue
            for directory in owner.iterdir():
                if not directory.is_dir():
                    continue
                modified = datetime.fromtimestamp(directory.stat().st_mtime, UTC)
                if modified < cutoff:
                    shutil.rmtree(directory, ignore_errors=True)
                    removed += 1
        return removed


__all__ = ["StagedUpload", "UploadStaging"]
