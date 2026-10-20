"""``orin code`` sessions: Claude Code's session ids (UUIDs) for Orin conversations.

A session is one Orin Code conversation started from a terminal or an SDK host. Claude Code names sessions with
UUIDs and resumes them by id or name (``-r``), or the latest one in the folder (``-c``); the index here maps each id
to its conversation, the folder it ran in and its name. It lives in Orin's data folder, owner-only, and is written
atomically.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

INDEX_FILE_NAME = "cli-sessions.json"
MAX_SESSIONS = 2000
_lock = threading.Lock()


def folder_key(path: Path | str) -> str:
    """How two spellings of the same folder compare (resolved, case-folded where the OS folds case)."""
    return os.path.normcase(str(Path(path).resolve()))


def is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError, TypeError):
        return False


@dataclass(frozen=True, slots=True)
class CliSession:
    session_id: str
    conversation_id: str
    folder: str
    name: str | None
    created_at: str
    updated_at: str


class SessionIndex:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / INDEX_FILE_NAME

    def load(self) -> list[CliSession]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        sessions: list[CliSession] = []
        for item in raw.get("sessions", []) if isinstance(raw, dict) else []:
            try:
                sessions.append(CliSession(**{key: item.get(key) for key in CliSession.__slots__}))
            except (TypeError, AttributeError):
                continue
        return [item for item in sessions if isinstance(item.session_id, str) and isinstance(item.conversation_id, str)]

    def _write(self, sessions: list[CliSession]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex[:8]}.tmp")
        data = json.dumps({"sessions": [asdict(item) for item in sessions[-MAX_SESSIONS:]]}, indent=1)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(temporary, self.path)

    def find(self, reference: str) -> CliSession | None:
        """By session id, by conversation id, or by name (the most recent session with that name)."""
        reference = (reference or "").strip()
        if not reference:
            return None
        sessions = self.load()
        for item in sessions:
            if item.session_id == reference.lower() or item.conversation_id == reference:
                return item
        named = [item for item in sessions if item.name == reference]
        return max(named, key=lambda item: item.updated_at) if named else None

    def latest_for(self, folder: Path | str) -> CliSession | None:
        key = folder_key(folder)
        here = [item for item in self.load() if item.folder == key]
        return max(here, key=lambda item: item.updated_at) if here else None

    def record(self, *, conversation_id: str, folder: Path | str, session_id: str | None = None, name: str | None = None) -> CliSession:
        """Add a session or touch an existing one (same id): its name changes only when a new one is given."""
        now = datetime.now(UTC).isoformat()
        with _lock:
            sessions = self.load()
            chosen = (session_id or str(uuid.uuid4())).lower()
            existing = next((item for item in sessions if item.session_id == chosen), None)
            if existing is not None and existing.conversation_id != conversation_id:
                raise ValueError("that session id already belongs to another conversation")
            if existing is not None:
                updated = replace(existing, updated_at=now, name=name if name else existing.name)
                sessions = [updated if item.session_id == chosen else item for item in sessions]
            else:
                updated = CliSession(chosen, conversation_id, folder_key(folder), name or None, now, now)
                sessions.append(updated)
            self._write(sessions)
            return updated


__all__ = ["CliSession", "INDEX_FILE_NAME", "SessionIndex", "folder_key", "is_uuid"]
