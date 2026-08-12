"""Durable, non-secret runtime preferences shared by API and chat workers."""
from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from agentos.installation import orin_paths


class AgentRuntimeSettingsStore:
    """Persist per-user turn-loop limits without coupling workers to the API process."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else orin_paths().data / "agent-runtime.json"
        self._lock = RLock()

    def get(self, user_id: str) -> dict[str, int | None]:
        self._validate_user(user_id)
        with self._lock:
            raw = self._read().get(user_id, {})
            value = raw.get("max_iterations") if isinstance(raw, dict) else None
            return {"max_iterations": value if isinstance(value, int) and value > 0 else None}

    def set_max_iterations(self, user_id: str, max_iterations: int | None) -> dict[str, int | None]:
        self._validate_user(user_id)
        if max_iterations is not None and (not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations < 1):
            raise ValueError("max_iterations must be a positive integer or null")
        with self._lock:
            values = self._read()
            values[user_id] = {"max_iterations": max_iterations}
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
            temporary.replace(self._path)
            return {"max_iterations": max_iterations}

    def _read(self) -> dict[str, object]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _validate_user(user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must be non-blank")


__all__ = ["AgentRuntimeSettingsStore"]
