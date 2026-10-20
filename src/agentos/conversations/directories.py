"""Folders a person added to one conversation with ``/add-dir`` (W4).

Every folder is checked by ``agentic.directories.check_directory`` before it is stored, and again by the turn
that uses it (it may have been removed or replaced since). ``take_unannounced`` hands each new folder to the
next turn once, which fires the ``DirectoryAdded`` hook for it.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agentos.agentic.directories import MAX_ADDITIONAL_DIRECTORIES, DirectoryError, check_directory
from agentos.persistence.postgres.schema import conversation_directories, conversations


class ConversationDirectoryStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        conversation_directories.create(engine, checkfirst=True)

    def _owned(self, connection, user_id: str, conversation_id: str) -> None:
        owner = connection.execute(select(conversations.c.user_id).where(conversations.c.conversation_id == conversation_id)).scalar_one_or_none()
        if owner is None or owner != user_id:
            raise LookupError(conversation_id)

    @staticmethod
    def _public(row) -> dict[str, object]:
        return {"directory_id": str(row["directory_id"]), "path": str(row["path"]), "created_at": row["created_at"].isoformat()}

    def list(self, user_id: str, conversation_id: str) -> list[dict[str, object]]:
        with self._engine.connect() as connection:
            self._owned(connection, user_id, conversation_id)
            rows = connection.execute(select(conversation_directories).where(
                conversation_directories.c.conversation_id == conversation_id, conversation_directories.c.user_id == user_id,
            ).order_by(conversation_directories.c.id)).mappings().all()
        return [self._public(row) for row in rows]

    def paths(self, user_id: str, conversation_id: str) -> list[str]:
        try:
            return [str(item["path"]) for item in self.list(user_id, conversation_id)]
        except LookupError:
            return []

    def add(self, user_id: str, conversation_id: str, raw: str, *, workspace_root: Path) -> tuple[dict[str, object], bool]:
        """``(directory, created)``; ``DirectoryError`` when the folder cannot be a working directory."""
        resolved = check_directory(raw, workspace_root=workspace_root)
        key = os.path.normcase(str(resolved))[:1024]
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            self._owned(connection, user_id, conversation_id)
            existing = connection.execute(select(conversation_directories).where(
                conversation_directories.c.conversation_id == conversation_id, conversation_directories.c.path_key == key,
            )).mappings().first()
            if existing is not None:
                return self._public(existing), False
            count = connection.execute(select(func.count()).select_from(conversation_directories).where(
                conversation_directories.c.conversation_id == conversation_id,
            )).scalar_one()
            if count >= MAX_ADDITIONAL_DIRECTORIES:
                raise DirectoryError(f"a conversation has at most {MAX_ADDITIONAL_DIRECTORIES} additional directories")
            directory_id = f"dir_{uuid4().hex[:16]}"
            try:
                connection.execute(insert(conversation_directories).values(
                    directory_id=directory_id, conversation_id=conversation_id, user_id=user_id,
                    path=str(resolved)[:1024], path_key=key, created_at=now, announced_at=None,
                ))
            except IntegrityError:
                raise DirectoryError("that folder was just added") from None
        return {"directory_id": directory_id, "path": str(resolved), "created_at": now.isoformat()}, True

    def remove(self, user_id: str, conversation_id: str, directory_id: str) -> bool:
        with self._engine.begin() as connection:
            self._owned(connection, user_id, conversation_id)
            result = connection.execute(delete(conversation_directories).where(
                conversation_directories.c.conversation_id == conversation_id, conversation_directories.c.user_id == user_id,
                conversation_directories.c.directory_id == directory_id,
            ))
        return bool(result.rowcount)

    def take_unannounced(self, user_id: str, conversation_id: str, usable: Callable[[str], bool] | None = None) -> list[str]:
        """Folders added since the last turn started, marked as announced (each is returned once).

        With ``usable`` (W7, W4), only the folders the turn can use are taken; the others stay unannounced, so they are
        announced the first time a turn can use them instead of never.
        """
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            rows = connection.execute(select(conversation_directories.c.id, conversation_directories.c.path).where(
                conversation_directories.c.conversation_id == conversation_id, conversation_directories.c.user_id == user_id,
                conversation_directories.c.announced_at.is_(None),
            ).order_by(conversation_directories.c.id)).all()
            if usable is not None:
                rows = [row for row in rows if usable(str(row.path))]
            if rows:
                connection.execute(update(conversation_directories).where(
                    conversation_directories.c.id.in_([row.id for row in rows]),
                ).values(announced_at=now))
        return [str(row.path) for row in rows]


__all__ = ["ConversationDirectoryStore"]
