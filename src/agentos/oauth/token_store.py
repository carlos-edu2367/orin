"""Encrypted persistence for OAuth tokens.

Reuses ``ProviderSecretCipher`` — the same Fernet cipher already protecting
provider API keys and MCP server secrets — so a token at rest is never
plaintext, and a worker restart can still decrypt it as long as
``AGENTOS_PROVIDER_ENCRYPTION_KEY``/``APP_MASTER_KEY`` is set.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from agentos.persistence.postgres.schema import oauth_tokens
from agentos.persistence.provider_secrets import ProviderSecretCipher

from .flow import OAuthTokens


def _now() -> datetime:
    return datetime.now(UTC)


def _cipher() -> ProviderSecretCipher:
    return ProviderSecretCipher.from_environment(required=True)


def _aware(value: datetime | None) -> datetime | None:
    # SQLite hands DateTime back without tzinfo; every value this store writes is UTC.
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class StoredOAuthTokens:
    access_token: str
    refresh_token: str | None
    scope: str | None
    expires_at: datetime | None
    version: int = 0


class OAuthTokenStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, *, user_id: str, provider_id: str, tokens: OAuthTokens) -> None:
        cipher = _cipher()
        now = _now()
        expires_at = now + timedelta(seconds=tokens.expires_in) if tokens.expires_in is not None else None
        values = {
            "user_id": user_id,
            "provider_id": provider_id,
            "access_token_ciphertext": cipher.encrypt(tokens.access_token),
            "refresh_token_ciphertext": cipher.encrypt(tokens.refresh_token) if tokens.refresh_token else None,
            "scope": tokens.scope,
            "expires_at": expires_at,
            "created_at": now,
            "updated_at": now,
            "version": 0,
            "refresh_lease_until": None,
        }
        insert = sqlite_insert if self._engine.dialect.name == "sqlite" else postgres_insert
        update_columns = {key: values[key] for key in (
            "access_token_ciphertext", "refresh_token_ciphertext", "scope", "expires_at", "updated_at",
        )}
        update_columns["version"] = oauth_tokens.c.version + 1
        update_columns["refresh_lease_until"] = None
        statement = insert(oauth_tokens).values(**values)
        statement = statement.on_conflict_do_update(index_elements=["user_id", "provider_id"], set_=update_columns)
        with self._engine.begin() as connection:
            connection.execute(statement)

    def get(self, *, user_id: str, provider_id: str) -> StoredOAuthTokens | None:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(oauth_tokens).where(oauth_tokens.c.user_id == user_id, oauth_tokens.c.provider_id == provider_id)
            ).mappings().one_or_none()
        if row is None:
            return None
        cipher = _cipher()
        return StoredOAuthTokens(
            access_token=cipher.decrypt(row["access_token_ciphertext"]),
            refresh_token=cipher.decrypt(row["refresh_token_ciphertext"]) if row["refresh_token_ciphertext"] else None,
            scope=row["scope"],
            expires_at=_aware(row["expires_at"]),
            version=int(row["version"] or 0),
        )

    def try_acquire_refresh_lease(self, *, user_id: str, provider_id: str, version: int, ttl: timedelta) -> bool:
        """Claim the right to refresh this grant; False when another process holds it or already refreshed."""
        now = _now()
        with self._engine.begin() as connection:
            result = connection.execute(
                update(oauth_tokens)
                .where(
                    oauth_tokens.c.user_id == user_id,
                    oauth_tokens.c.provider_id == provider_id,
                    oauth_tokens.c.version == version,
                    or_(oauth_tokens.c.refresh_lease_until.is_(None), oauth_tokens.c.refresh_lease_until < now),
                )
                .values(refresh_lease_until=now + ttl)
            )
        return result.rowcount == 1

    def release_refresh_lease(self, *, user_id: str, provider_id: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(oauth_tokens)
                .where(oauth_tokens.c.user_id == user_id, oauth_tokens.c.provider_id == provider_id)
                .values(refresh_lease_until=None)
            )

    def delete(self, *, user_id: str, provider_id: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                delete(oauth_tokens).where(oauth_tokens.c.user_id == user_id, oauth_tokens.c.provider_id == provider_id)
            )


__all__ = ["OAuthTokenStore", "StoredOAuthTokens"]
