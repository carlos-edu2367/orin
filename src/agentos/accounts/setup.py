"""The one-time token that creates the first admin of a server instance.

Without it, whoever reaches a fresh public URL first would own the instance.
The raw token only ever exists in the API's log; the database keeps a digest.
"""
from __future__ import annotations

import secrets
from hashlib import sha256
from hmac import compare_digest

from sqlalchemy import delete, insert, select
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import instance_setup

from .clock import utcnow
from .errors import InvalidSetupToken, SetupCompleted
from .store import UserStore


def _digest(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


class SetupTokens:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def issue_if_needed(self, users: UserStore) -> str | None:
        has_accounts = users.count() > 0
        with self._engine.begin() as connection:
            connection.execute(delete(instance_setup))
            if has_accounts:
                return None
            token = secrets.token_urlsafe(32)
            connection.execute(insert(instance_setup).values(token_digest=_digest(token), created_at=utcnow()))
            return token

    def verify(self, token: str) -> bool:
        with self._engine.connect() as connection:
            stored = connection.execute(select(instance_setup.c.token_digest)).scalar_one_or_none()
        return stored is not None and bool(token) and compare_digest(stored, _digest(token))

    def consume(self, token: str, users: UserStore) -> None:
        if users.count() > 0:
            raise SetupCompleted("this instance already has an account")
        if not self.verify(token):
            raise InvalidSetupToken("setup token is invalid")
        with self._engine.begin() as connection:
            connection.execute(delete(instance_setup))


__all__ = ["SetupTokens"]
