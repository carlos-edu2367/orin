"""Durable override for which model performs visual reading on someone's behalf.

This backs the `/v1/settings/vision-model` routes only. The worker reads the
same `vision_model_selections` table through its own already-open engine
(`agentos.workers.chat.ChatWorker._vision_override`) rather than through this
store -- the two never share a connection, but they agree on the table.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import vision_model_selections


@dataclass(frozen=True, slots=True)
class VisionModelSelection:
    provider: str
    model_id: str


class PostgresVisionModelSettingsStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, user_id: str) -> VisionModelSelection | None:
        statement = select(vision_model_selections.c.provider, vision_model_selections.c.model_id).where(
            vision_model_selections.c.user_id == user_id
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return VisionModelSelection(str(row["provider"]), str(row["model_id"])) if row else None

    def set(self, user_id: str, provider: str, model_id: str) -> None:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            updated = connection.execute(
                update(vision_model_selections)
                .where(vision_model_selections.c.user_id == user_id)
                .values(provider=provider, model_id=model_id, updated_at=now)
            ).rowcount
            if not updated:
                connection.execute(
                    insert(vision_model_selections).values(user_id=user_id, provider=provider, model_id=model_id, updated_at=now)
                )

    def clear(self, user_id: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(delete(vision_model_selections).where(vision_model_selections.c.user_id == user_id))
