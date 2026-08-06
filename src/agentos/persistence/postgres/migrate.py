from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, Engine


MIGRATIONS_PATH = Path(__file__).with_name("migrations")


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


__all__ = ["MIGRATIONS_PATH", "upgrade"]
