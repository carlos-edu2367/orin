"""Durable binding between an effective workspace id and a local folder."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import workspace_roots


class PostgresLocalWorkspaceStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def root_for(self, workspace_id: str, user_id: str) -> str | None:
        statement = select(workspace_roots.c.root_path).where(
            workspace_roots.c.workspace_id == workspace_id,
            workspace_roots.c.user_id == user_id,
        )
        with self._engine.connect() as connection:
            return connection.execute(statement).scalar_one_or_none()

    def set_root(self, workspace_id: str, user_id: str, root_path: str) -> None:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            updated = connection.execute(
                update(workspace_roots)
                .where(workspace_roots.c.workspace_id == workspace_id, workspace_roots.c.user_id == user_id)
                .values(root_path=root_path, updated_at=now)
            ).rowcount
            if not updated:
                connection.execute(insert(workspace_roots).values(workspace_id=workspace_id, user_id=user_id, root_path=root_path, created_at=now, updated_at=now))

    def clear_root(self, workspace_id: str, user_id: str) -> bool:
        statement = delete(workspace_roots).where(
            workspace_roots.c.workspace_id == workspace_id,
            workspace_roots.c.user_id == user_id,
        )
        with self._engine.begin() as connection:
            return bool(connection.execute(statement).rowcount)
