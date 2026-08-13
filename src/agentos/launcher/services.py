"""Prepare the local SQLite datastore and bring its schema to head."""

from __future__ import annotations

from dataclasses import dataclass

from agentos.installation import RuntimeProfile
from agentos.persistence.sqlite import create_local_engine

from .environment import RuntimeEnvironment
from .probes import ProbeResult, sqlite_probe


class ServicesUnavailable(RuntimeError):
    """The local datastore could not be opened, with an actionable message."""


@dataclass(frozen=True, slots=True)
class DatastoreStatus:
    database: ProbeResult

    @property
    def ready(self) -> bool:
        return bool(self.database)


def probe_datastores(environment: RuntimeEnvironment) -> DatastoreStatus:
    return DatastoreStatus(sqlite_probe(environment.database_url))


def ensure_datastores(
    environment: RuntimeEnvironment,
    profile: RuntimeProfile,
    *,
    log,
) -> DatastoreStatus:
    """Open the user-owned SQLite database; no external service is started."""
    del profile
    status = probe_datastores(environment)
    if status.ready:
        log.debug("local SQLite datastore reachable")
        return status
    raise ServicesUnavailable(f"The local SQLite database could not be opened: {status.database.detail}")


def apply_migrations(environment: RuntimeEnvironment, profile: RuntimeProfile, *, log) -> None:
    """Bring the local database schema to head from packaged migrations."""
    from alembic import command
    from alembic.config import Config

    migrations = profile.migrations
    if not (migrations / "env.py").is_file():
        raise ServicesUnavailable(
            f"Database migrations are missing from this installation (expected {migrations})."
        )
    engine = create_local_engine(environment.database_url)
    try:
        with engine.begin() as connection:
            config = Config()
            config.set_main_option("script_location", str(migrations))
            config.attributes["connection"] = connection
            log.info("applying local SQLite migrations from %s", migrations)
            command.upgrade(config, "head")
    finally:
        engine.dispose()


__all__ = [
    "DatastoreStatus",
    "ServicesUnavailable",
    "apply_migrations",
    "ensure_datastores",
    "probe_datastores",
]
