"""The small, local contract between the Orin supervisor and Electron.

Electron is deliberately a display host, not another process supervisor.  The
launcher writes one complete JSON snapshot atomically whenever startup changes;
the splash reads that snapshot on a short polling interval.  A snapshot is more
robust than a pipe or a localhost server: it survives an Electron restart and
also leaves a useful final error for the user to act on.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agentos.installation import OrinPaths

StartupState = Literal["pending", "starting", "ready", "error", "stopped"]

SERVICE_ORDER = (
    "database",
    "migrations",
    "backend",
    "health",
    "ready",
    "worker",
    "scheduler",
    "frontend",
)


@dataclass(slots=True)
class StartupService:
    state: StartupState = "pending"
    detail: str = ""


@dataclass(slots=True)
class StartupSnapshot:
    version: int
    mode: StartupState
    message: str
    updated_at: str
    url: str | None
    logs_dir: str
    shutdown_file: str
    restart_command: list[str]
    services: dict[str, StartupService]


class DesktopStatusWriter:
    """Publish human-safe desktop startup state without touching service logic."""

    filename = "desktop-startup.json"

    def __init__(self, paths: OrinPaths, *, restart_command: tuple[str, ...]) -> None:
        self.path = paths.run / self.filename
        self._snapshot = StartupSnapshot(
            version=1,
            mode="starting",
            message="Preparando seu ambiente",
            updated_at=_now(),
            url=None,
            logs_dir=str(paths.logs),
            shutdown_file=str(paths.stop_request),
            restart_command=list(restart_command),
            services={service: StartupService() for service in SERVICE_ORDER},
        )
        self._write()

    @property
    def snapshot(self) -> StartupSnapshot:
        return self._snapshot

    def set_url(self, url: str) -> None:
        self._snapshot.url = url
        self._touch()

    def service(self, name: str, state: StartupState, detail: str = "") -> None:
        # Pre-standalone development snapshots used ``docker``. Accept it only
        # while reading/testing old callers; published snapshots expose the
        # accurate local ``database`` stage.
        if name == "docker":
            name = "database"
        if name not in self._snapshot.services:
            raise ValueError(f"unknown desktop startup service: {name}")
        self._snapshot.services[name] = StartupService(state, _brief(detail))
        self._touch()

    def ready(self, url: str) -> None:
        self._snapshot.mode = "ready"
        self._snapshot.message = "Orin está pronto"
        self._snapshot.url = url
        self._touch()

    def failed(self, service: str, message: str) -> None:
        self.service(service, "error", message)
        self._snapshot.mode = "error"
        self._snapshot.message = _brief(message)
        self._touch()

    def stopped(self, message: str = "A inicialização foi cancelada.") -> None:
        self._snapshot.mode = "stopped"
        self._snapshot.message = message
        self._touch()

    def _touch(self) -> None:
        self._snapshot.updated_at = _now()
        self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self._snapshot), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def _brief(message: str, maximum: int = 500) -> str:
    """Keep a splash useful without leaking a traceback or overwhelming it."""
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    value = " ".join(lines)
    return value[:maximum] + ("…" if len(value) > maximum else "")


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = ["DesktopStatusWriter", "SERVICE_ORDER", "StartupService", "StartupSnapshot", "StartupState"]
