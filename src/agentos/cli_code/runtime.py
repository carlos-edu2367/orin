"""The local Orin ``orin code`` drives: found (or started) through the launcher's run directory, reached with the token.

Only the local profile (spec §9.1): the instance must say it is local and has host folders, and must accept this run's
token. A shared Orin (SERVER) is refused, from the CLI's own environment and from what the instance reports.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx

from agentos.configuration.mode import RuntimeMode, current_mode
from agentos.installation import OrinPaths, orin_paths, runtime_profile
from agentos.installation.api_token import read_api_token
from agentos.launcher.state import InstanceState, running_instance

from .errors import CliRuntimeError

START_TIMEOUT_SECONDS = 180.0
_POLL_SECONDS = 0.5
_REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


class OrinClient:
    """The local API with this run's bearer token; every mutating call carries a fresh idempotency key."""

    def __init__(self, base_url: str, token: str, *, http: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._http = http or httpx.Client(base_url=self.base_url, timeout=_REQUEST_TIMEOUT, trust_env=False)

    def __repr__(self) -> str:  # never shows the token
        return f"OrinClient({self.base_url!r})"

    def _headers(self, mutating: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if mutating:
            headers["Idempotency-Key"] = f"cli-{uuid.uuid4().hex}"
        return headers

    def request(self, method: str, path: str, *, body: Any = None, params: dict[str, str] | None = None,
                timeout: float | None = None) -> Any:
        mutating = method.upper() not in {"GET", "HEAD"}
        try:
            response = self._http.request(method, path, json=body, params=params, headers=self._headers(mutating),
                                          **({"timeout": timeout} if timeout is not None else {}))
        except httpx.HTTPError as error:
            raise CliRuntimeError(f"the local Orin did not answer ({type(error).__name__})") from error
        if response.status_code >= 400:
            raise CliRuntimeError(_api_error(method, path, response))
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise CliRuntimeError(f"{method} {path}: the local Orin answered with something that is not JSON") from error

    def get(self, path: str, **params: str) -> Any:
        return self.request("GET", path, params=params or None)

    def post(self, path: str, body: Any = None, *, timeout: float | None = None) -> Any:
        return self.request("POST", path, body=body if body is not None else {}, timeout=timeout)

    def patch(self, path: str, body: Any) -> Any:
        return self.request("PATCH", path, body=body)

    def put(self, path: str, body: Any) -> Any:
        return self.request("PUT", path, body=body)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    def events(self, conversation_id: str, *, after: str = "0", stop: Callable[[], bool] | None = None) -> Iterator[dict[str, Any]]:
        """The conversation's activity as it happens (the API's SSE stream); reconnects where it left off."""
        cursor = after
        while not (stop and stop()):
            try:
                with self._http.stream(
                    "GET", f"/v1/conversations/{conversation_id}/events", params={"after": cursor},
                    headers=self._headers(False), timeout=httpx.Timeout(None, connect=5.0),
                ) as response:
                    if response.status_code >= 400:
                        response.read()
                        raise CliRuntimeError(_api_error("GET", "events", response))
                    kind = ""
                    for line in response.iter_lines():
                        if stop and stop():
                            return
                        if line.startswith("event: "):
                            kind = line[7:].strip()
                        elif line.startswith("data: "):
                            data = json.loads(line[6:])
                            if kind == "heartbeat":
                                cursor = str(data.get("cursor") or cursor)
                                yield {"event_type": "heartbeat", "cursor": cursor}
                                continue
                            if kind == "resync":
                                raise CliRuntimeError("the conversation's event stream lost its place")
                            if isinstance(data, dict):
                                cursor = str(data.get("cursor") or cursor)
                                yield data
            except httpx.HTTPError as error:
                if stop and stop():
                    return
                raise CliRuntimeError(f"the conversation's event stream broke ({type(error).__name__})") from error

    def close(self) -> None:
        self._http.close()


def _api_error(method: str, path: str, response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
        detail = str(error.get("code") or error.get("message") or "")
    except (ValueError, AttributeError):
        detail = ""
    return f"{method} {path} was refused by the local Orin ({response.status_code}{': ' + detail if detail else ''})"


def _launcher_command() -> list[str]:
    command = list(runtime_profile().launcher_command())
    executable = Path(command[0])
    if executable.stem.lower() == "orin-code":  # a frozen copy: start the runtime with the real orin binary beside it
        sibling = executable.with_name("orin" + executable.suffix)
        if sibling.is_file():
            command[0] = str(sibling)
    return command


def _spawn_runtime(paths: OrinPaths) -> None:
    """``orin start --no-browser`` in the background, detached from this terminal."""
    command = [*_launcher_command(), "start", "--no-browser"]
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        base = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        try:  # outlive a job the calling terminal or SDK host may kill on exit
            subprocess.Popen(command, creationflags=base | breakaway, **kwargs)
            return
        except OSError:
            pass
        subprocess.Popen(command, creationflags=base, **kwargs)
    else:
        subprocess.Popen(command, start_new_session=True, **kwargs)


def _healthy(state: InstanceState) -> bool:
    try:
        return httpx.get(f"{state.url}/healthz", timeout=2.0, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


def find_or_start_runtime(
    paths: OrinPaths | None = None,
    *,
    start: bool = True,
    timeout: float = START_TIMEOUT_SECONDS,
    spawn: Callable[[OrinPaths], None] = _spawn_runtime,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> InstanceState:
    paths = paths or orin_paths()
    state = running_instance(paths)
    if state is not None and _healthy(state):
        return state
    if state is None:
        if not start:
            raise CliRuntimeError("Orin is not running (start it with `orin start`)")
        spawn(paths)
    deadline = clock() + timeout
    while clock() < deadline:
        state = running_instance(paths)
        if state is not None and _healthy(state):
            return state
        sleep(_POLL_SECONDS)
    raise CliRuntimeError(f"Orin did not start within {int(timeout)} s; see {paths.service_log('launcher')}")


def connect_local(paths: OrinPaths | None = None, *, start: bool = True, **runtime_options: Any) -> OrinClient:
    """A client of this person's local Orin, started when it is not running; SERVER is refused."""
    if current_mode() is RuntimeMode.SERVER:
        raise CliRuntimeError("orin code works with a local Orin only; this environment is a shared Orin (ORIN_MODE=server)")
    paths = paths or orin_paths()
    state = find_or_start_runtime(paths, start=start, **runtime_options)
    token = read_api_token(paths.run)
    if not token:
        raise CliRuntimeError("the running Orin issued no token for orin code; restart it with `orin restart`")
    client = OrinClient(state.url, token)
    try:
        info = client.get("/v1/instance")
    except CliRuntimeError:
        client.close()
        raise
    capabilities = info.get("capabilities") if isinstance(info, dict) else None
    if not isinstance(info, dict) or info.get("mode") != "local" or not (isinstance(capabilities, dict) and capabilities.get("host_folders")):
        client.close()
        raise CliRuntimeError("orin code works with a local Orin only; this one is a shared Orin")
    if info.get("credential") != "cli_token":
        client.close()
        raise CliRuntimeError("the local Orin did not accept this run's token")
    return client


__all__ = ["OrinClient", "connect_local", "find_or_start_runtime"]
