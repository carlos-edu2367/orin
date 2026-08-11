from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import conversation_prompts


class PostgresConversationPromptStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, user_id: str, message: str) -> str:
        reference = f"prompt:{uuid4().hex}"
        with self._engine.begin() as connection:
            connection.execute(insert(conversation_prompts).values(
                prompt_ref=reference, user_id=user_id, message=message, created_at=datetime.now(UTC),
            ))
        return reference
