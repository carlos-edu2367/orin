"""Production ``ProviderConfigurationApplication`` adapter (frontend Fase D).

Satisfies ``agentos.api.contracts.ProviderConfigurationApplication`` over a
dedicated ``provider_configurations`` table, scoped by ``(user_id,
provider)`` — there is no execution/agent scope for a provider credential
(BACKEND_CAPABILITY_MATRIX.md/BACKEND_DISCOVERY.md have never described one;
it is configured once per user, independent of any execution), so this does
not reuse ``persistence_records``'s execution-shaped ownership tuple (see
"Decisões locais" in docs/frontend/IMPLEMENTATION_PLAN.md, Fase D).

The API key is never included in any returned dict: ``configure``/
``inspect``/``revoke`` all return a small public projection
(``provider``/``enabled``/``model``/``secret_ref``), matching the shape
``FakeProviderConfiguration`` already establishes in
``tests/unit/api/test_api_asgi.py``. ``agentos.api.gateway._provider_public``
additionally strips any field whose name contains ``api_key``/``secret``/
``token``/``password``/``credential`` server-side, so this is defense in
depth, not the only guard.

``configure`` is a plain upsert scoped by ``(user_id, provider)``: PUT is
already idempotent by HTTP semantics (repeating the same PUT converges to
the same stored state), so there is no separate idempotency-key ledger here
— unlike execution commands, a provider configuration has no
non-execution-scoped idempotency store to reuse, and inventing one for a
single-row upsert would add nothing PUT doesn't already give for free.

Storing the API key in cleartext (rather than encrypted at rest) mirrors
this codebase's only other place a provider API key is configured today —
``agentos.bootstrap.production.ProductionSettings`` reads it from a plain
environment variable, with no field-level encryption. No encryption-at-rest
utility exists anywhere in this codebase to reuse or extend within Fase D's
scope; adding one would be new infrastructure beyond what Fase D asks for.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from agentos.api.contracts import ApplicationNotFoundError

from .schema import provider_configurations


def _public(row) -> dict[str, object]:
    return {
        "provider": str(row["provider"]),
        "enabled": bool(row["enabled"]),
        "model": str(row["model"]),
        "secret_ref": str(row["secret_ref"]),
    }


class PostgresProviderConfigurationAdapter:
    """Production adapter for the ``ProviderConfigurationApplication`` port (frontend Fase D)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def configure(self, command: dict[str, object]) -> dict[str, object]:
        provider = str(command["provider"])
        user_id = str(command["user_id"])
        model = str(command["model"])
        enabled = bool(command["enabled"])
        api_key = str(command["api_key"])
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(provider_configurations).where(
                    provider_configurations.c.user_id == user_id,
                    provider_configurations.c.provider == provider,
                )
            ).mappings().first()
            if existing is None:
                secret_ref = f"provider-secret:{uuid4().hex}"
                connection.execute(
                    insert(provider_configurations).values(
                        user_id=user_id, provider=provider, enabled=enabled, model=model,
                        api_key=api_key, secret_ref=secret_ref, created_at=now, updated_at=now,
                    )
                )
                row = {"provider": provider, "enabled": enabled, "model": model, "secret_ref": secret_ref}
            else:
                connection.execute(
                    update(provider_configurations).where(provider_configurations.c.id == existing["id"]).values(
                        enabled=enabled, model=model, api_key=api_key, updated_at=now,
                    )
                )
                row = {"provider": provider, "enabled": enabled, "model": model, "secret_ref": existing["secret_ref"]}
        return _public(row)

    def inspect(self, query: dict[str, object]) -> dict[str, object]:
        provider = str(query["provider"])
        user_id = str(query["user_id"])
        with self._engine.connect() as connection:
            row = connection.execute(
                select(provider_configurations).where(
                    provider_configurations.c.user_id == user_id,
                    provider_configurations.c.provider == provider,
                )
            ).mappings().first()
        if row is None:
            raise ApplicationNotFoundError(provider)
        return _public(row)

    def revoke(self, command: dict[str, object]) -> dict[str, object]:
        provider = str(command["provider"])
        user_id = str(command["user_id"])
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(provider_configurations).where(
                    provider_configurations.c.user_id == user_id,
                    provider_configurations.c.provider == provider,
                )
            ).mappings().first()
            if existing is None:
                raise ApplicationNotFoundError(provider)
            connection.execute(
                update(provider_configurations).where(provider_configurations.c.id == existing["id"]).values(
                    enabled=False, updated_at=datetime.now(UTC),
                )
            )
        return _public({**existing, "enabled": False})


__all__ = ["PostgresProviderConfigurationAdapter"]
