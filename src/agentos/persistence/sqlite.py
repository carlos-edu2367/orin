"""SQLite engine configuration for the local, single-user Orin runtime."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine


def sqlite_url(path: Path) -> str:
    """Return an absolute SQLite URL without relying on the current directory."""
    return f"sqlite+pysqlite:///{path.resolve().as_posix()}"


def create_local_engine(database_url: str) -> Engine:
    """Create the runtime engine and apply the local durability invariants."""
    if not database_url.startswith("sqlite"):
        raise ValueError("The local Orin runtime requires a SQLite DATABASE_URL.")
    engine = create_engine(
        database_url,
        future=True,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record) -> None:  # pragma: no cover - exercised by callers
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    return engine


__all__ = ["create_local_engine", "sqlite_url"]
