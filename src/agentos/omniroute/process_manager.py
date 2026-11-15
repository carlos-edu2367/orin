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

from agentos.installation import orin_paths
from agentos.process_identity import kill_verified, process_created, same_process

# The gateway is a Node application that reads its own configuration and
# provider catalog before it serves anything. On a cold start that takes tens of
# seconds on an ordinary machine, and giving up early does not merely report a
# failure — it kills a process that was about to work.
DEFAULT_STARTUP_TIMEOUT = 45.0
HEALTH_REQUEST_TIMEOUT = 5.0


def _startup_timeout() -> float:
    try:
        value = float(os.getenv("AGENTOS_OMNIROUTE_STARTUP_TIMEOUT", "").strip() or DEFAULT_STARTUP_TIMEOUT)
    except ValueError:
        return DEFAULT_STARTUP_TIMEOUT
    return value if value > 0 else DEFAULT_STARTUP_TIMEOUT


def _terminate_tree(pid: int) -> bool:
    import subprocess
    from subprocess import DEVNULL, run

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        return run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=DEVNULL, stderr=DEVNULL, check=False, creationflags=creationflags).returncode == 0  # noqa: S603, S607
    except OSError:
        return False


class _Process(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...


class OmniRouteRuntimeSettingsStore:
    """Small atomic, non-secret local preference store.

    Provider credentials deliberately stay in the encrypted provider store;
    this file holds only the user's boolean startup preference.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else orin_paths().data / "omniroute-runtime.json"
        self._lock = RLock()

    def auto_start(self, user_id: str) -> bool:
        with self._lock:
            return bool(self._read().get(user_id, False))

    def any_enabled(self) -> bool:
        """Whether *any* user asked for OmniRoute to start with Orin.

        This is a local desktop installation, so startup happens before a user is
        known. The launcher reads this to decide whether OmniRoute is part of the
        startup plan at all; it is never written outside the settings endpoint.
        """
        with self._lock:
            return any(self._read().values())

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
        owner_file: str | Path | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = base_url.rstrip("/")
        configured = os.getenv("AGENTOS_OMNIROUTE_COMMAND", "").strip()
        self._command = tuple(command or tuple(configured.split()) or self._detected_command())
        self._health = health or self._request_health
        self._start_process = start_process or self._spawn
        self._wait_ready = wait_ready or self._wait_for_health
        self._owner_file = self._resolve_owner_file(settings, owner_file)
        self._process: _Process | None = None
        self._lock = RLock()
        self._last_failure: str | None = None

    @staticmethod
    def _resolve_owner_file(settings: OmniRouteRuntimeSettingsStore, owner_file: str | Path | None) -> Path:
        if owner_file is not None:
            return Path(owner_file)
        default_settings_path = orin_paths().data / "omniroute-runtime.json"
        settings_path = getattr(settings, "_path", None)
        if isinstance(settings_path, Path) and settings_path != default_settings_path:
            return settings_path.with_name("omniroute-owner.json")
        return orin_paths().run / "omniroute-owner.json"

    def auto_start(self, user_id: str) -> bool:
        return self._settings.auto_start(user_id)

    @property
    def base_url(self) -> str:
        """The gateway endpoint this manager supervises."""
        return self._base_url

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
        if not self._settings.any_enabled():
            return self.status()
        return self.start()

    def start_if_any_enabled_in_background(self) -> dict[str, object]:
        """Start the optional gateway without holding up AgentOS readiness.

        The normal ``start`` contract waits for the gateway's model endpoint so
        an interactive request can report a final state.  That wait can be 45s
        on a cold machine, which must never run inside FastAPI's startup hook:
        the local API needs to expose ``/healthz`` while OmniRoute catches up.
        """
        if not self._settings.any_enabled():
            return self.status()
        healthy = self._healthy()
        with self._lock:
            if healthy:
                return {"state": "external", "ownership": "external"} if not self._owned_alive() else {"state": "ready", "ownership": "agentos"}
            if self._owned_alive():
                return {"state": "starting", "ownership": "agentos"}
            try:
                self._process = self._start_process(self._command)
            except (OSError, ValueError) as error:
                self._last_failure = str(error) or "OmniRoute could not be started"
                return {"state": "failed", "ownership": None}
            self._record_owner(getattr(self._process, "pid", None))
            return {"state": "starting", "ownership": "agentos"}

    def status(self) -> dict[str, object]:
        healthy = self._healthy()
        with self._lock:
            owned = self._owned_alive()
            if healthy:
                return {"state": "ready", "ownership": "agentos" if owned else "external"}
            if owned:
                return {"state": "starting", "ownership": "agentos"}
            return {"state": "stopped", "ownership": None, **({"failure": self._last_failure} if self._last_failure else {})}

    def start(self) -> dict[str, object]:
        healthy = self._healthy()
        with self._lock:
            if healthy:
                return {"state": "external", "ownership": "external"} if not self._owned_alive() else {"state": "ready", "ownership": "agentos"}
            if self._owned_alive():
                return {"state": "starting", "ownership": "agentos"}
            try:
                self._process = self._start_process(self._command)
            except (OSError, ValueError) as error:
                self._last_failure = str(error) or "OmniRoute could not be started"
                return {"state": "failed", "ownership": None}
            self._record_owner(getattr(self._process, "pid", None))
        ready = self._wait_ready()
        with self._lock:
            if ready:
                self._last_failure = None
                return {"state": "ready", "ownership": "agentos"}
            if self._owned_alive():
                # Slow is not broken. A cold gateway can take minutes to serve
                # its first request, and killing it at the timeout guarantees it
                # never finishes. Ownership is kept so Stop and Restart still
                # act on this exact process.
                self._last_failure = None
                return {"state": "starting", "ownership": "agentos"}
            self._last_failure = "OmniRoute exited before it became ready"
            self._terminate_owned()
            return {"state": "failed", "ownership": None}

    def stop(self) -> dict[str, object]:
        with self._lock:
            if self._owned_alive():
                self._terminate_owned()
                return {"state": "stopped", "ownership": None}
        return {"state": "external", "ownership": "external"} if self._healthy() else {"state": "stopped", "ownership": None}

    def restart(self) -> dict[str, object]:
        healthy = self._healthy()
        with self._lock:
            if not self._owned_alive() and healthy:
                return {"state": "external", "ownership": "external"}
            self._terminate_owned()
        return self.start()

    def _healthy(self) -> bool:
        try:
            return bool(self._health())
        except Exception:
            return False

    def _record_owner(self, pid: object) -> None:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            self._clear_owner()
            return
        created = process_created(pid)
        if created is None or isinstance(created, bool) or (isinstance(created, str) and not created.strip()):
            self._clear_owner()
            return
        try:
            self._owner_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._owner_file.with_suffix(".tmp")
            temporary.write_text(json.dumps({"pid": pid, "created": created}), encoding="utf-8")
            temporary.replace(self._owner_file)
        except OSError:
            return

    def _read_owner(self) -> tuple[int, int | float | str] | None:
        try:
            raw = json.loads(self._owner_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            self._clear_owner()
            return None
        if not isinstance(raw, dict):
            self._clear_owner()
            return None
        pid = raw.get("pid")
        created = raw.get("created")
        valid_pid = isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
        valid_created = (
            (isinstance(created, (int, float)) and not isinstance(created, bool) and created > 0)
            or (isinstance(created, str) and bool(created.strip()))
        )
        if not valid_pid or not valid_created:
            self._clear_owner()
            return None
        return pid, created

    def _clear_owner(self) -> None:
        try:
            self._owner_file.unlink(missing_ok=True)
        except OSError:
            return

    def _owned_alive(self) -> bool:
        if self._process is not None:
            if self._process.poll() is None:
                return True
            self._process = None
            self._clear_owner()
            return False
        owner = self._read_owner()
        if owner is None:
            return False
        pid, created = owner
        if same_process(pid, created):
            return True
        self._clear_owner()
        return False

    def _terminate_owned(self) -> None:
        process = self._process
        self._process = None
        owner = self._read_owner()
        self._clear_owner()
        if process is not None:
            if process.poll() is None:
                if owner is not None and kill_verified(owner[0], owner[1]):
                    return
                if os.name == "nt" and _terminate_tree(process.pid):
                    return
                try:
                    process.terminate()
                except Exception:
                    pass
            return
        if owner is not None:
            kill_verified(owner[0], owner[1])

    def _request_health(self) -> bool:
        try:
            response = httpx.get(f"{self._base_url}/models", timeout=HEALTH_REQUEST_TIMEOUT)
            return 200 <= response.status_code < 300
        except httpx.HTTPError:
            return False

    def _wait_for_health(self) -> bool:
        deadline = monotonic() + _startup_timeout()
        while monotonic() < deadline:
            if self._healthy():
                return True
            sleep(0.25)
        return False

    @staticmethod
    def _spawn(command: tuple[str, ...]) -> _Process:
        import subprocess

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        return Popen(command, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, creationflags=creationflags)  # noqa: S603

    @staticmethod
    def _detected_command() -> tuple[str, ...]:
        # npm exposes a `.cmd` shim on Windows. Prefer it over the PowerShell
        # shim so child creation does not depend on a shell or execution policy.
        executable = shutil.which("omniroute.cmd") if os.name == "nt" else None
        executable = executable or shutil.which("omniroute") or "omniroute"
        return (executable,)
