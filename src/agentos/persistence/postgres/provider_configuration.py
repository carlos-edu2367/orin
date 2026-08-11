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
  (``provider``/``enabled``/``secret_ref``), matching the shape
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
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.provider_catalog.omniroute import DEFAULT_OMNIROUTE_BASE_URL, normalize_omniroute_base_url
from agentos.provider_catalog.omniroute import OmniRouteCatalogClient
from agentos.provider_catalog.installation import OmniRouteInstaller


def _public(row) -> dict[str, object]:
    return {
        "provider": str(row["provider"]),
        "enabled": bool(row["enabled"]),
        "secret_ref": str(row["secret_ref"]),
        "catalog_refreshed_at": row.get("catalog_refreshed_at"),
        **({"base_url": row["base_url"]} if str(row["provider"]) == "omniroute" and row.get("base_url") else {}),
    }


class PostgresProviderConfigurationAdapter:
    """Production adapter for the ``ProviderConfigurationApplication`` port (frontend Fase D)."""

    def __init__(self, engine: Engine, *, cipher: ProviderSecretCipher | None = None, installer: OmniRouteInstaller | None = None) -> None:
        self._engine = engine
        self._cipher = cipher or ProviderSecretCipher.from_environment()
        self._installer = installer or OmniRouteInstaller()

    def configure(self, command: dict[str, object]) -> dict[str, object]:
        provider = str(command["provider"])
        user_id = str(command["user_id"])
        enabled = bool(command["enabled"])
        api_key = _api_key(provider, command.get("api_key"))
        base_url = _base_url(provider, command.get("base_url"))
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
                        user_id=user_id, provider=provider, enabled=enabled,
                        api_key=None, api_key_ciphertext=self._cipher.encrypt(api_key, allow_empty=provider == "omniroute"), base_url=base_url, secret_ref=secret_ref, catalog_refreshed_at=None, created_at=now, updated_at=now,
                    )
                )
                row = {"provider": provider, "enabled": enabled, "base_url": base_url, "secret_ref": secret_ref, "catalog_refreshed_at": None}
            else:
                connection.execute(
                    update(provider_configurations).where(provider_configurations.c.id == existing["id"]).values(
                        enabled=enabled, api_key=None, api_key_ciphertext=self._cipher.encrypt(api_key, allow_empty=provider == "omniroute"), base_url=base_url, updated_at=now,
                    )
                )
                row = {"provider": provider, "enabled": enabled, "base_url": base_url, "secret_ref": existing["secret_ref"], "catalog_refreshed_at": existing["catalog_refreshed_at"]}
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

    def test_connection(self, command: dict[str, object]) -> dict[str, object]:
        if str(command.get("provider")) != "omniroute":
            raise ValueError("connection testing is only available for OmniRoute")
        base_url = _base_url("omniroute", command.get("base_url"))
        try:
            models = OmniRouteCatalogClient().fetch(str(command["api_key"]), base_url=str(base_url))
        except RuntimeError as error:
            # The adapter only permits the gateway's safe generic response.
            raise ValueError("OmniRoute connection failed") from error
        return {"connected": True, "models_available": len(models), "base_url": base_url}

    def install(self, command: dict[str, object]) -> dict[str, object]:
        if str(command.get("provider")) != "omniroute":
            raise ValueError("installation is only available for OmniRoute")
        return self._installer.install()

    def installation_status(self, query: dict[str, object]) -> dict[str, object]:
        if str(query.get("provider")) != "omniroute":
            raise ValueError("installation status is only available for OmniRoute")
        return self._installer.installation_status()


__all__ = ["PostgresProviderConfigurationAdapter"]


def _base_url(provider: str, value: object) -> str | None:
    if provider != "omniroute":
        return None
    return normalize_omniroute_base_url(str(value or DEFAULT_OMNIROUTE_BASE_URL))


def _api_key(provider: str, value: object) -> str:
    api_key = str(value or "")
    if provider != "omniroute" and len(api_key) < 4:
        raise ValueError("provider API key is required")
    return api_key
