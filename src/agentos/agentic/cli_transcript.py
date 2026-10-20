"""The tool inputs and results of an ``orin code`` turn, kept apart from the activity log (W6).

Activity payloads are sanitized for every viewer: strings are bounded at 512 characters and keys such as ``prompt``
or ``headers`` are redacted, so a real tool result cannot ride an activity event. A CLI turn's tool events carry a
``transcript_id`` instead, and the entry itself is written here — one owner-only JSON file per tool event under
``<ORIN_DATA_DIR>/cli-transcripts/<conversation>`` — for ``orin code`` to read back through the API with its token.
Entries older than ``RETENTION_SECONDS`` are removed whenever a conversation's folder is written to.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

RETENTION_SECONDS = 7 * 24 * 3600
MAX_ENTRY_BYTES = 200_000
_ID = re.compile(r"^tr_[0-9a-f]{32}$")


def transcripts_root() -> Path:
    from agentos.installation.paths import orin_paths

    return orin_paths().data / "cli-transcripts"


def _folder(root: Path, conversation_id: str) -> Path:
    digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:32]
    return root / digest


class TranscriptStore:
    def __init__(self, conversation_id: str, *, root: Path | None = None, clock=time.time) -> None:
        if not conversation_id:
            raise ValueError("a transcript needs its conversation")
        self.folder = _folder(root or transcripts_root(), conversation_id)
        self._clock = clock

    def write(self, entry: dict[str, Any]) -> str | None:
        """Store one entry and return its id; None when it could not be written (the turn goes on)."""
        try:
            data = json.dumps(entry, ensure_ascii=False, default=str).encode("utf-8")
            if len(data) > MAX_ENTRY_BYTES:
                return None
            self.folder.mkdir(parents=True, exist_ok=True)
            self._prune()
            entry_id = f"tr_{uuid.uuid4().hex}"
            descriptor = os.open(self.folder / f"{entry_id}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            return entry_id
        except (OSError, TypeError, ValueError):
            return None

    def read(self, entry_id: str) -> dict[str, Any] | None:
        if not isinstance(entry_id, str) or not _ID.fullmatch(entry_id):
            return None
        try:
            data = json.loads((self.folder / f"{entry_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _prune(self) -> None:
        cutoff = self._clock() - RETENTION_SECONDS
        try:
            for item in self.folder.iterdir():
                try:
                    if item.suffix == ".json" and item.stat().st_mtime < cutoff:
                        item.unlink()
                except OSError:
                    continue
        except OSError:
            return


__all__ = ["MAX_ENTRY_BYTES", "RETENTION_SECONDS", "TranscriptStore", "transcripts_root"]
