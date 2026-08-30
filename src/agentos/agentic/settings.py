"""Durable, non-secret runtime preferences shared by API and chat workers."""
from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from agentos.installation import orin_paths
from agentos.code_mode.models import CodeAutonomy, CodeModeSettings


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
            current = values.get(user_id)
            record = dict(current) if isinstance(current, dict) else {}
            record["max_iterations"] = max_iterations
            values[user_id] = record
            self._write(values)
            return {"max_iterations": max_iterations}

    def get_code_mode(self, user_id: str) -> CodeModeSettings:
        """Return global, non-secret Code mode preferences with safe defaults."""
        self._validate_user(user_id)
        with self._lock:
            raw = self._read().get(user_id, {})
            record = raw if isinstance(raw, dict) else {}
            try:
                autonomy = CodeAutonomy(str(record.get("code_autonomy") or CodeAutonomy.APPROVAL_REQUIRED.value))
            except ValueError:
                autonomy = CodeAutonomy.APPROVAL_REQUIRED
            return CodeModeSettings(
                autonomy=autonomy,
                system_notifications=record.get("code_system_notifications") is True,
                monitoring_enabled=record.get("code_monitoring_enabled") is not False,
            )

    def set_code_mode(
        self,
        user_id: str,
        *,
        autonomy: CodeAutonomy | str,
        system_notifications: bool,
        monitoring_enabled: bool,
    ) -> CodeModeSettings:
        self._validate_user(user_id)
        settings = CodeModeSettings(
            autonomy=CodeAutonomy(autonomy),
            system_notifications=system_notifications,
            monitoring_enabled=monitoring_enabled,
        )
        with self._lock:
            values = self._read()
            current = values.get(user_id)
            record = dict(current) if isinstance(current, dict) else {}
            record.update({
                "code_autonomy": settings.autonomy.value,
                "code_system_notifications": settings.system_notifications,
                "code_monitoring_enabled": settings.monitoring_enabled,
            })
            values[user_id] = record
            self._write(values)
        return settings

    def _read(self) -> dict[str, object]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write(self, values: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
        temporary.replace(self._path)

    @staticmethod
    def _validate_user(user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must be non-blank")


__all__ = ["AgentRuntimeSettingsStore"]
