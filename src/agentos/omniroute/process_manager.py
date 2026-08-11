"""Safe local lifecycle control for OmniRoute.

The process is optional and remains separate from the AgentOS lifetime.  We
only terminate the exact child AgentOS created; a healthy gateway found before
startup is treated as external even when its command line is indistinguishable.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from subprocess import DEVNULL, Popen
from threading import RLock
from time import monotonic, sleep
from typing import Callable, Protocol, Sequence

import httpx


class _Process(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...


class OmniRouteRuntimeSettingsStore:
    """Small atomic, non-secret local preference store.

    Provider credentials deliberately stay in the encrypted provider store;
    this file holds only the user's boolean startup preference.
    """

    def __init__(self, path: str | Path = "data/omniroute-runtime.json") -> None:
        self._path = Path(path)
        self._lock = RLock()

    def auto_start(self, user_id: str) -> bool:
        with self._lock:
            return bool(self._read().get(user_id, False))

    def set_auto_start(self, user_id: str, value: bool) -> bool:
        if not user_id.strip():
            raise ValueError("user_id must be non-blank")
        with self._lock:
            values = self._read()
            values[user_id] = bool(value)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
            temporary.replace(self._path)
            return bool(value)

    def _read(self) -> dict[str, bool]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(key): bool(value) for key, value in raw.items() if isinstance(key, str)}


class OmniRouteProcessManager:
    def __init__(
        self,
        settings: OmniRouteRuntimeSettingsStore,
        *,
        base_url: str = "http://localhost:20128/v1",
        command: Sequence[str] | None = None,
        health: Callable[[], bool] | None = None,
        start_process: Callable[[tuple[str, ...]], _Process] | None = None,
        wait_ready: Callable[[], bool] | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = base_url.rstrip("/")
        configured = os.getenv("AGENTOS_OMNIROUTE_COMMAND", "").strip()
        self._command = tuple(command or tuple(configured.split()) or self._detected_command())
        self._health = health or self._request_health
        self._start_process = start_process or self._spawn
        self._wait_ready = wait_ready or self._wait_for_health
        self._process: _Process | None = None
        self._lock = RLock()
        self._last_failure: str | None = None

    def auto_start(self, user_id: str) -> bool:
        return self._settings.auto_start(user_id)

    def set_auto_start(self, user_id: str, value: bool) -> dict[str, object]:
        return {"auto_start": self._settings.set_auto_start(user_id, value)}

    def start_if_enabled(self, user_id: str) -> dict[str, object]:
        if not self.auto_start(user_id):
            return self.status()
        return self.start()

    def start_if_any_enabled(self) -> dict[str, object]:
        """Attempt automatic startup once at AgentOS boot without knowing a user.

        This is a local desktop process: the endpoint remains per-user, while
        startup uses the persisted preferences collectively and is intentionally
        best-effort.
        """
        with self._lock:
            if not any(self._settings._read().values()):
                return self.status()
        return self.start()

    def status(self) -> dict[str, object]:
        with self._lock:
            if self._healthy():
                return {"state": "ready", "ownership": "agentos" if self._owned_alive() else "external"}
            if self._owned_alive():
                return {"state": "starting", "ownership": "agentos"}
            return {"state": "stopped", "ownership": None, **({"failure": self._last_failure} if self._last_failure else {})}

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._healthy():
                return {"state": "external", "ownership": "external"} if not self._owned_alive() else {"state": "ready", "ownership": "agentos"}
            if self._owned_alive():
                return {"state": "starting", "ownership": "agentos"}
            try:
                self._process = self._start_process(self._command)
            except (OSError, ValueError) as error:
                self._last_failure = str(error) or "OmniRoute could not be started"
                return {"state": "failed", "ownership": None}
            if self._wait_ready():
                self._last_failure = None
                return {"state": "ready", "ownership": "agentos"}
            self._last_failure = "OmniRoute did not become ready before the startup timeout"
            self._terminate_owned()
            return {"state": "failed", "ownership": None}

    def stop(self) -> dict[str, object]:
        with self._lock:
            if not self._owned_alive():
                return {"state": "external", "ownership": "external"} if self._healthy() else {"state": "stopped", "ownership": None}
            self._terminate_owned()
            return {"state": "stopped", "ownership": None}

    def restart(self) -> dict[str, object]:
        with self._lock:
            if not self._owned_alive() and self._healthy():
                return {"state": "external", "ownership": "external"}
            self._terminate_owned()
            return self.start()

    def _healthy(self) -> bool:
        try:
            return bool(self._health())
        except Exception:
            return False

    def _owned_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _terminate_owned(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()

    def _request_health(self) -> bool:
        try:
            response = httpx.get(f"{self._base_url}/models", timeout=1.5)
            return 200 <= response.status_code < 300
        except httpx.HTTPError:
            return False

    def _wait_for_health(self) -> bool:
        deadline = monotonic() + 12
        while monotonic() < deadline:
            if self._healthy():
                return True
            sleep(0.25)
        return False

    @staticmethod
    def _spawn(command: tuple[str, ...]) -> _Process:
        return Popen(command, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)  # noqa: S603

    @staticmethod
    def _detected_command() -> tuple[str, ...]:
        # npm exposes a `.cmd` shim on Windows. Prefer it over the PowerShell
        # shim so child creation does not depend on a shell or execution policy.
        executable = shutil.which("omniroute.cmd") if os.name == "nt" else None
        executable = executable or shutil.which("omniroute") or "omniroute"
        return (executable,)
