"""Readiness checks.

Every step of startup waits for evidence that the thing it started is actually
usable — an HTTP response, a row in a table, a socket that accepts. Nothing here
sleeps for a fixed duration and hopes; a sleep is only the interval between two
real checks, and every wait has a deadline and a reason it can report.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from typing import Callable
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ready: bool
    detail: str

    def __bool__(self) -> bool:
        return self.ready


def wait_until(
    check: Callable[[], ProbeResult],
    *,
    timeout: float,
    interval: float = 0.25,
    abort: Callable[[], str | None] | None = None,
) -> ProbeResult:
    """Poll ``check`` until it succeeds, the deadline passes, or ``abort`` fires.

    ``abort`` is how a dead child short-circuits a long timeout: there is no
    point waiting thirty seconds for a health endpoint whose process already
    exited with a traceback.
    """
    deadline = monotonic() + timeout
    last = ProbeResult(False, "not checked yet")
    while True:
        if abort is not None:
            reason = abort()
            if reason:
                return ProbeResult(False, reason)
        last = check()
        if last.ready:
            return last
        if monotonic() >= deadline:
            return ProbeResult(False, f"timed out after {timeout:.0f}s: {last.detail}")
        sleep(interval)


def tcp_probe(host: str, port: int, *, timeout: float = 1.0) -> ProbeResult:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return ProbeResult(True, f"{host}:{port} accepting connections")
    except OSError as error:
        return ProbeResult(False, f"{host}:{port} unreachable ({error.strerror or error})")


def http_probe(url: str, *, timeout: float = 2.0, expect: Callable[[int, str], bool] | None = None) -> ProbeResult:
    """A plain HTTP GET.

    ``httpx`` is already a dependency, but importing it lazily keeps the launcher
    startup path free of the provider stack.
    """
    import httpx

    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=False)
    except httpx.HTTPError as error:
        return ProbeResult(False, f"{url} not answering ({type(error).__name__})")
    body = ""
    if expect is not None:
        try:
            body = response.text
        except Exception:  # pragma: no cover - defensive; body is local and small
            body = ""
        if not expect(response.status_code, body):
            return ProbeResult(False, f"{url} returned {response.status_code} but did not look ready")
        return ProbeResult(True, f"{url} ready")
    if 200 <= response.status_code < 300:
        return ProbeResult(True, f"{url} returned {response.status_code}")
    return ProbeResult(False, f"{url} returned {response.status_code}")


def postgres_probe(dsn: str, *, timeout: float = 3.0) -> ProbeResult:
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(dsn, pool_pre_ping=True, connect_args={"connect_timeout": int(timeout)})
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        finally:
            engine.dispose()
        return ProbeResult(True, "postgres answering")
    except Exception as error:
        return ProbeResult(False, f"postgres unavailable ({type(error).__name__})")


def redis_probe(url: str, *, timeout: float = 3.0) -> ProbeResult:
    try:
        import redis

        client = redis.Redis.from_url(url, socket_connect_timeout=timeout, socket_timeout=timeout)
        try:
            if client.ping():
                return ProbeResult(True, "redis answering")
        finally:
            client.close()
        return ProbeResult(False, "redis did not answer PING")
    except Exception as error:
        return ProbeResult(False, f"redis unavailable ({type(error).__name__})")


def heartbeat_probe(dsn: str, components: tuple[str, ...], *, maximum_age: timedelta = timedelta(seconds=30)) -> ProbeResult:
    """Whether the given runtime components reported in recently.

    The publisher and the chat worker both write ``runtime_heartbeats``. That row
    is the only signal that says the process not only started but reached its
    loop with a working database connection, which is what "ready" has to mean.
    """
    try:
        from sqlalchemy import create_engine, select

        from agentos.persistence.postgres.schema import runtime_heartbeats

        engine = create_engine(dsn, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                rows = dict(
                    connection.execute(
                        select(runtime_heartbeats.c.component, runtime_heartbeats.c.updated_at).where(
                            runtime_heartbeats.c.component.in_(components)
                        )
                    ).all()
                )
        finally:
            engine.dispose()
    except Exception as error:
        return ProbeResult(False, f"heartbeat table unreadable ({type(error).__name__})")

    now = datetime.now(UTC)
    missing = []
    for component in components:
        updated = rows.get(component)
        if updated is None:
            missing.append(f"{component} has never reported")
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        if now - updated > maximum_age:
            missing.append(f"{component} last reported {(now - updated).total_seconds():.0f}s ago")
    if missing:
        return ProbeResult(False, "; ".join(missing))
    return ProbeResult(True, "workers reporting")


def host_port_from_url(url: str, default_port: int) -> tuple[str, int]:
    parts = urlsplit(url)
    return parts.hostname or "127.0.0.1", parts.port or default_port


def frontend_probe(base_url: str, *, timeout: float = 3.0) -> ProbeResult:
    """The built SPA is actually being served, not merely the API.

    A backend that boots with a missing or broken web build answers ``/healthz``
    perfectly well and then hands the browser a 404. Opening a tab on that is
    worse than reporting the failure here.
    """

    def looks_like_the_app(status: int, body: str) -> bool:
        return status == 200 and "<div id=\"root\"" in body.replace("'", '"')

    return http_probe(base_url.rstrip("/") + "/", timeout=timeout, expect=looks_like_the_app)


__all__ = [
    "ProbeResult",
    "frontend_probe",
    "heartbeat_probe",
    "host_port_from_url",
    "http_probe",
    "postgres_probe",
    "redis_probe",
    "tcp_probe",
    "wait_until",
]
