"""The background half of an update: download and verify while Orin keeps working.

``Updater.prepare`` does the slow, safe part (nothing the running installation
uses is touched). This wraps it in one observable job, polled by the app, and
reports the state a person cares about: downloading with a percentage, ready to
restart, failed with a reason. Activation is deliberately *not* here: it has to
stop the very process that would be running it, so the desktop shell hands it to
``orin update --apply`` instead.
"""

from __future__ import annotations

from threading import Lock, Thread
from typing import Any, Callable

from .profile import RuntimeProfile
from .updater import UpdateError, UpdateEvent, Updater
from .versions import installation_root

_STATE_BY_STEP = {
    "check": "checking", "download": "downloading", "verify": "verifying",
    "extract": "extracting", "validate": "validating",
}


class UpdateJob:
    def __init__(self, profile: RuntimeProfile, *, updater_factory: Callable[..., Updater] = Updater) -> None:
        self._profile = profile
        self._factory = updater_factory
        self._lock = Lock()
        self._thread: Thread | None = None
        self._state = "idle"
        self._progress: float | None = None
        self._bytes: tuple[int | None, int | None] = (None, None)
        self._speed: float | None = None
        self._label: str | None = None
        self._error: dict[str, Any] | None = None
        self._version: str | None = None

    def _updater(self, emit: Callable[[UpdateEvent], None] | None = None) -> Updater | None:
        root = installation_root(self._profile)
        if self._profile.kind != "installed" or root is None:
            return None
        return self._factory(versions_root=root, current_version=self._profile.version, emit=emit)

    def status(self) -> dict[str, Any]:
        updater = self._updater()
        base: dict[str, Any] = {"current_version": self._profile.version}
        if updater is None:
            return {**base, "state": "unsupported"}
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            snapshot = {
                "state": self._state, "progress": self._progress, "bytes_done": self._bytes[0], "bytes_total": self._bytes[1],
                "speed": self._speed, "label": self._label, "version": self._version, "error": self._error,
            }
        if not running and snapshot["state"] in ("idle", "checking", "downloading", "verifying", "extracting", "validating", "ready"):
            prepared = updater.prepared_release()
            if prepared is not None:
                snapshot.update(state="ready", version=prepared.version, notes=prepared.notes, release_url=prepared.release_url, progress=1.0)
            elif snapshot["state"] != "idle":
                snapshot.update(state="idle")
        base.update({key: value for key, value in snapshot.items() if value is not None})
        attempt = updater.last_attempt()
        if attempt is not None:
            base["last_attempt"] = attempt
        return base

    def start(self) -> dict[str, Any]:
        updater = self._updater()
        if updater is None:
            return self.status()
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._state, self._progress, self._bytes, self._speed = "checking", None, (None, None), None
                self._label, self._error, self._version = "Procurando a versão mais recente", None, None
                self._thread = Thread(target=self._run, name="orin-update-prepare", daemon=True)
                self._thread.start()
        return self.status()

    def _run(self) -> None:
        updater = self._updater(self._observe)
        assert updater is not None
        try:
            result = updater.prepare()
        except UpdateError as error:
            self._finish("failed", error={"message": error.message, "hint": error.hint, "step": error.step})
            return
        except Exception as error:  # noqa: BLE001 - a background job must report, never die silently
            self._finish("failed", error={"message": f"Erro inesperado na atualização ({type(error).__name__}).", "hint": "Tente de novo."})
            return
        self._finish("up_to_date" if result.status == "up_to_date" else "ready", version=result.version)

    def _observe(self, event: UpdateEvent) -> None:
        with self._lock:
            state = _STATE_BY_STEP.get(event.step)
            if state is not None:
                self._state = state
            self._label = event.label
            if event.phase == "progress":
                self._progress, self._bytes, self._speed = event.progress, (event.bytes_done, event.bytes_total), event.speed
            elif event.phase == "start":
                self._progress, self._speed = None, None
            if event.step == "check" and event.phase == "done" and event.detail:
                self._version = event.detail.removeprefix("versão ")

    def _finish(self, state: str, *, version: str | None = None, error: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._state, self._error, self._label = state, error, None
            if version:
                self._version = version
            self._progress = 1.0 if state == "ready" else self._progress


_jobs: dict[int, UpdateJob] = {}


def update_job(profile: RuntimeProfile) -> UpdateJob:
    """One job per process: two clicks must not start two downloads."""
    return _jobs.setdefault(id(profile), UpdateJob(profile))


__all__ = ["UpdateJob", "update_job"]
