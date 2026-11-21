from __future__ import annotations

import os
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, Engine, make_url


MIGRATIONS_PATH = Path(__file__).with_name("migrations")


def backup_before_migration(bind: Engine | Connection | str, tag: str = "pre-0064") -> Path | None:
    """Create an atomic SQLite backup copy ``<db>.<tag>.bak`` before destructive migrations.

    Returns the backup path if ``bind`` targets an existing SQLite file, or ``None`` for
    in-memory SQLite / non-SQLite databases. Never overwrites an existing backup file.
    """
    if isinstance(bind, Engine):
        url = bind.url
    elif isinstance(bind, Connection):
        url = bind.engine.url
    else:
        url = make_url(str(bind))

    if not str(url.drivername or "").startswith("sqlite"):
        return None
    db_name = str(url.database or "").strip()
    if not db_name or db_name == ":memory:" or db_name.startswith("file::memory:"):
        return None

    db_path = Path(db_name)
    if not db_path.is_file():
        return None

    backup_path = Path(f"{db_path}.{tag}.bak")
    if backup_path.exists():
        return backup_path

    tmp_path = Path(f"{backup_path}.tmp")
    try:
        if tmp_path.exists():
            tmp_path.unlink()
        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(str(tmp_path))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        os.replace(tmp_path, backup_path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise
    return backup_path


def upgrade(bind: Engine | Connection | str, revision: str = "head") -> None:
    """Apply migrations explicitly; no adapter or Runtime path calls this."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_PATH))
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, revision)
        return
    if isinstance(bind, Connection):
        config.attributes["connection"] = bind
    else:
        config.set_main_option("sqlalchemy.url", bind)
    command.upgrade(config, revision)


def downgrade(bind: Engine | Connection | str, revision: str = "base") -> None:
    """Revert migrations explicitly as an administrative operation."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_PATH))
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, revision)
        return
    if isinstance(bind, Connection):
        config.attributes["connection"] = bind
    else:
        config.set_main_option("sqlalchemy.url", bind)
    command.downgrade(config, revision)


__all__ = ["MIGRATIONS_PATH", "backup_before_migration", "downgrade", "upgrade"]
