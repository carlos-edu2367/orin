"""The datastores Orin depends on, and the schema it expects to find in them.

This module is the seam where "Orin needs Postgres and Redis" is satisfied.
Today that means probing them and, if a Compose definition ships with this
installation, bringing them up. A future installed build swaps the Compose call
for a bundled datastore and nothing above this module changes — the supervisor
only ever asks for the step to succeed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from agentos.installation import RuntimeProfile

from .environment import RuntimeEnvironment
from .probes import ProbeResult, postgres_probe, redis_probe, wait_until


class ServicesUnavailable(RuntimeError):
    """The datastores could not be reached, explained in terms a user can act on."""


@dataclass(frozen=True, slots=True)
class DatastoreStatus:
    postgres: ProbeResult
    redis: ProbeResult
    started_here: bool

    @property
    def ready(self) -> bool:
        return bool(self.postgres) and bool(self.redis)


def probe_datastores(environment: RuntimeEnvironment) -> DatastoreStatus:
    return DatastoreStatus(postgres_probe(environment.database_url), redis_probe(environment.redis_url), False)


def _compose_up(compose_file: Path, timeout: float) -> tuple[bool, str]:
    for executable in (["docker", "compose"], ["docker-compose"]):
        try:
            result = subprocess.run(  # noqa: S603 - fixed executables, path from the runtime profile
                [*executable, "-f", str(compose_file), "up", "-d", "--wait"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return False, f"{' '.join(executable)} did not finish within {timeout:.0f}s"
        if result.returncode == 0:
            return True, "compose services are up"
        return False, (result.stderr or result.stdout or "").strip()[-800:]
    return False, "docker is not installed or not on PATH"


def ensure_datastores(
    environment: RuntimeEnvironment,
    profile: RuntimeProfile,
    *,
    log,
    timeout: float = 120.0,
) -> DatastoreStatus:
    """Make Postgres and Redis reachable, or explain precisely why not."""
    status = probe_datastores(environment)
    if status.ready:
        log.debug("datastores already reachable")
        return status

    compose_file = profile.compose_file
    if compose_file is None:
        raise ServicesUnavailable(_unreachable_message(status, None))

    log.info("starting datastores with %s", compose_file)
    started, detail = _compose_up(compose_file, timeout)
    if not started:
        raise ServicesUnavailable(_unreachable_message(status, detail))

    postgres = wait_until(lambda: postgres_probe(environment.database_url), timeout=60.0, interval=0.5)
    redis = wait_until(lambda: redis_probe(environment.redis_url), timeout=30.0, interval=0.5)
    status = DatastoreStatus(postgres, redis, True)
    if not status.ready:
        raise ServicesUnavailable(_unreachable_message(status, detail))
    return status


def _unreachable_message(status: DatastoreStatus, compose_detail: str | None) -> str:
    lines = ["Orin needs PostgreSQL and Redis and could not reach them."]
    if not status.postgres:
        lines.append(f"  PostgreSQL: {status.postgres.detail}")
    if not status.redis:
        lines.append(f"  Redis: {status.redis.detail}")
    if compose_detail:
        lines.append("")
        lines.append("Starting them automatically failed:")
        lines.append(f"  {compose_detail}")
    lines.append("")
    lines.append("Start Docker Desktop and try again, or point DATABASE_URL and REDIS_URL at instances you already run.")
    return "\n".join(lines)


def apply_migrations(environment: RuntimeEnvironment, profile: RuntimeProfile, *, log) -> None:
    """Bring the schema to head.

    Configured in code rather than from ``alembic.ini`` so that the scripts are
    found inside the package, wherever the process happens to be running.
    """
    from alembic import command
    from alembic.config import Config

    migrations = profile.migrations
    if not (migrations / "env.py").is_file():
        raise ServicesUnavailable(
            f"Database migrations are missing from this installation (expected {migrations})."
        )
    config = Config()
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("sqlalchemy.url", environment.database_url)
    log.info("applying migrations from %s", migrations)
    command.upgrade(config, "head")


__all__ = [
    "DatastoreStatus",
    "ServicesUnavailable",
    "apply_migrations",
    "ensure_datastores",
    "probe_datastores",
]
