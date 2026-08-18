"""Production adapter for per-provider API key storage and fallback pooling.

Sibling of ``provider_configuration.py``: that module owns one row of
provider-level settings (enabled/base_url/model/key_cooldown_seconds) per
``(user_id, provider)``; this module owns N credential rows per
``(user_id, provider)``, ordered by ``position`` (0 = principal, tried first
on every new request). See docs/superpowers/specs/
2026-08-18-multi-api-key-provider-fallback-design.md.

A key is never included in any returned dict beyond its ciphertext-free
public shape (``id``/``label``/``position``/``status``/``cooldown_until``);
``agentos.api.gateway._provider_key_public`` additionally allowlists exactly
those fields server-side, so this is defense in depth, not the only guard.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine

from agentos.api.contracts import ApplicationNotFoundError

from .schema import provider_api_keys
from agentos.persistence.provider_secrets import ProviderSecretCipher


@dataclass(frozen=True, slots=True)
class ProviderApiKeyCredential:
    """A decrypted key ready to build a transport with; never logged or returned to a client."""

    id: int
    plaintext: str


def _as_aware(value: datetime) -> datetime:
    """SQLite drops tzinfo on read even for a DateTime(timezone=True) column;
    Postgres does not. Normalize either back to aware UTC before comparing."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _public(row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "label": row["label"],
        "position": int(row["position"]),
        "status": str(row["status"]),
        "cooldown_until": row["cooldown_until"],
    }


class PostgresProviderApiKeyAdapter:
    """Production adapter for the ``ProviderApiKeyApplication`` port."""

    def __init__(self, engine: Engine, *, cipher: ProviderSecretCipher | None = None) -> None:
        self._engine = engine
        self._cipher = cipher or ProviderSecretCipher.from_environment()

    def list_keys(self, query: dict[str, object]) -> list[dict[str, object]]:
        provider, user_id = str(query["provider"]), str(query["user_id"])
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(provider_api_keys).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                ).order_by(provider_api_keys.c.position)
            ).mappings().all()
        return [_public(row) for row in rows]

    def add_key(self, command: dict[str, object]) -> dict[str, object]:
        provider, user_id = str(command["provider"]), str(command["user_id"])
        api_key = str(command.get("api_key") or "").strip()
        if len(api_key) < 4:
            raise ValueError("provider API key is required")
        label = command.get("label")
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            next_position = connection.execute(
                select(func.coalesce(func.max(provider_api_keys.c.position), -1) + 1).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                )
            ).scalar_one()
            secret_ref = f"provider-key-secret:{uuid4().hex}"
            result = connection.execute(
                insert(provider_api_keys).values(
                    user_id=user_id, provider=provider, label=str(label) if label else None,
                    api_key_ciphertext=self._cipher.encrypt(api_key), secret_ref=secret_ref,
                    position=next_position, status="active", cooldown_until=None,
                    created_at=now, updated_at=now,
                )
            )
            row_id = result.inserted_primary_key[0]
        return _public({"id": row_id, "label": str(label) if label else None, "position": next_position, "status": "active", "cooldown_until": None})

    def rename_key(self, command: dict[str, object]) -> dict[str, object]:
        provider, user_id, key_id = str(command["provider"]), str(command["user_id"]), int(command["key_id"])
        label = command.get("label")
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(provider_api_keys).where(
                    provider_api_keys.c.id == key_id, provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                )
            ).mappings().first()
            if existing is None:
                raise ApplicationNotFoundError(str(key_id))
            connection.execute(
                update(provider_api_keys).where(provider_api_keys.c.id == key_id).values(
                    label=str(label) if label else None, updated_at=datetime.now(UTC),
                )
            )
        return _public({**existing, "label": str(label) if label else None})

    def remove_key(self, command: dict[str, object]) -> None:
        provider, user_id, key_id = str(command["provider"]), str(command["user_id"]), int(command["key_id"])
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(provider_api_keys).where(
                    provider_api_keys.c.id == key_id, provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                )
            ).mappings().first()
            if existing is None:
                raise ApplicationNotFoundError(str(key_id))
            connection.execute(delete(provider_api_keys).where(provider_api_keys.c.id == key_id))
            remaining = connection.execute(
                select(provider_api_keys).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                ).order_by(provider_api_keys.c.position)
            ).mappings().all()
            for new_position, row in enumerate(remaining):
                if row["position"] != new_position:
                    connection.execute(
                        update(provider_api_keys).where(provider_api_keys.c.id == row["id"]).values(position=new_position)
                    )

    def reorder_keys(self, command: dict[str, object]) -> list[dict[str, object]]:
        provider, user_id = str(command["provider"]), str(command["user_id"])
        ordered_ids = [int(item) for item in command["ordered_ids"]]
        with self._engine.begin() as connection:
            existing_ids = connection.execute(
                select(provider_api_keys.c.id).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                )
            ).scalars().all()
            if set(existing_ids) != set(ordered_ids) or len(ordered_ids) != len(set(ordered_ids)):
                raise ValueError("ordered_ids must list every key for this provider exactly once")
            # Two passes avoid colliding with the (user_id, provider, position)
            # unique constraint while positions are still in flight.
            for offset, key_id in enumerate(ordered_ids):
                connection.execute(
                    update(provider_api_keys).where(provider_api_keys.c.id == key_id).values(position=1_000 + offset, updated_at=datetime.now(UTC))
                )
            for position, key_id in enumerate(ordered_ids):
                connection.execute(
                    update(provider_api_keys).where(provider_api_keys.c.id == key_id).values(position=position)
                )
        return self.list_keys({"provider": provider, "user_id": user_id})

    def next_available_key(self, user_id: str, provider: str, *, exclude: frozenset[int] = frozenset()) -> ProviderApiKeyCredential | None:
        """Earliest-position key not yet tried; falls back to the earliest-position
        key overall (ignoring cooldown) once every untried key is in cooldown."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(provider_api_keys).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider,
                ).order_by(provider_api_keys.c.position)
            ).mappings().all()
        candidates = [row for row in rows if row["id"] not in exclude]
        if not candidates:
            return None
        now = datetime.now(UTC)
        available = [row for row in candidates if row["status"] != "cooldown" or row["cooldown_until"] is None or _as_aware(row["cooldown_until"]) <= now]
        chosen = available[0] if available else candidates[0]
        return ProviderApiKeyCredential(id=int(chosen["id"]), plaintext=self._cipher.decrypt(str(chosen["api_key_ciphertext"])))

    def mark_cooldown(self, key_id: int, cooldown_seconds: int) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(provider_api_keys).where(provider_api_keys.c.id == key_id).values(
                    status="cooldown", cooldown_until=datetime.now(UTC) + timedelta(seconds=cooldown_seconds), updated_at=datetime.now(UTC),
                )
            )

    def mark_active(self, key_id: int) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(provider_api_keys).where(provider_api_keys.c.id == key_id).values(
                    status="active", cooldown_until=None, updated_at=datetime.now(UTC),
                )
            )

    def set_primary_key(self, command: dict[str, object]) -> dict[str, object]:
        """Upsert the position-0 key. The seam ``configure()``'s legacy single-key
        ``apiKey`` field targets, so ``PUT /v1/providers/{provider}`` keeps working
        exactly as it did before this feature existed."""
        provider, user_id = str(command["provider"]), str(command["user_id"])
        api_key = str(command["api_key"])
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(provider_api_keys).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider, provider_api_keys.c.position == 0,
                )
            ).mappings().first()
            ciphertext = self._cipher.encrypt(api_key)
            if existing is None:
                secret_ref = f"provider-key-secret:{uuid4().hex}"
                result = connection.execute(
                    insert(provider_api_keys).values(
                        user_id=user_id, provider=provider, label=None, api_key_ciphertext=ciphertext,
                        secret_ref=secret_ref, position=0, status="active", cooldown_until=None,
                        created_at=now, updated_at=now,
                    )
                )
                row_id = result.inserted_primary_key[0]
            else:
                connection.execute(
                    update(provider_api_keys).where(provider_api_keys.c.id == existing["id"]).values(
                        api_key_ciphertext=ciphertext, status="active", cooldown_until=None, updated_at=now,
                    )
                )
                row_id = existing["id"]
        return _public({"id": row_id, "label": None, "position": 0, "status": "active", "cooldown_until": None})

    def clear_primary_key(self, command: dict[str, object]) -> None:
        """Delete the position-0 key, mirroring ``configure()`` being called with
        an empty key for a provider whose key is optional (local Ollama, OmniRoute)."""
        provider, user_id = str(command["provider"]), str(command["user_id"])
        with self._engine.begin() as connection:
            connection.execute(
                delete(provider_api_keys).where(
                    provider_api_keys.c.user_id == user_id, provider_api_keys.c.provider == provider, provider_api_keys.c.position == 0,
                )
            )


__all__ = ["PostgresProviderApiKeyAdapter", "ProviderApiKeyCredential"]
