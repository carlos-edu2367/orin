"""Production ``ProviderConfigurationApplication`` adapter (frontend Fase D).

Satisfies ``agentos.api.contracts.ProviderConfigurationApplication`` over a
dedicated ``provider_configurations`` table, scoped by ``(user_id,
provider)`` — there is no execution/agent scope for a provider credential
(BACKEND_CAPABILITY_MATRIX.md/BACKEND_DISCOVERY.md have never described one;
it is configured once per user, independent of any execution), so this does
not reuse ``persistence_records``'s execution-shaped ownership tuple (see
"Decisões locais" in docs/frontend/IMPLEMENTATION_PLAN.md, Fase D).

This table holds only provider-level settings: ``enabled``/``base_url``/
``key_cooldown_seconds``. Credentials live in ``provider_api_keys``
(``agentos.persistence.postgres.provider_api_keys``), one row per key, so a
provider can have more than one — see docs/superpowers/specs/
2026-08-18-multi-api-key-provider-fallback-design.md. ``configure()``'s
``api_key`` field is kept for backward compatibility: it upserts the
position-0 ("principal") key through ``PostgresProviderApiKeyAdapter``.
Every credential is encrypted at rest via ``ProviderSecretCipher``
(``enc:v1:...``); a plaintext value is never stored.

The API key is never included in any returned dict: ``configure``/
``inspect``/``revoke`` all return a small public projection
  (``provider``/``enabled``/``secret_ref``/``key_cooldown_seconds``),
matching the shape ``FakeProviderConfiguration`` already establishes in
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
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from agentos.api.contracts import ApplicationNotFoundError, ProviderCredentialRejectedError

from .schema import provider_configurations
from .provider_api_keys import PostgresProviderApiKeyAdapter
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.provider_catalog.models import PROVIDERS_WITH_BASE_URL, PROVIDERS_WITH_OPTIONAL_KEY
from agentos.provider_catalog.nvidia import (
    DEFAULT_NVIDIA_BASE_URL,
    NvidiaAuthenticationError,
    NvidiaModelCatalogClient,
    is_nvidia_cloud,
    normalize_nvidia_base_url,
)
from agentos.provider_catalog.ollama import (
    DEFAULT_OLLAMA_BASE_URL,
    OllamaCatalogClient,
    OllamaCloudAuthenticationError,
    is_ollama_cloud,
    normalize_ollama_base_url,
)
from agentos.provider_catalog.omniroute import (
    DEFAULT_OMNIROUTE_BASE_URL,
    OmniRouteCatalogClient,
    normalize_omniroute_base_url,
)
from agentos.provider_catalog.opencode import (
    DEFAULT_OPENCODE_LOCAL_BASE_URL,
    OPENCODE_CLOUD_BASE_URL,
    OpenCodeModelCatalogClient,
    is_opencode_cloud,
    normalize_opencode_base_url,
)
from agentos.provider_catalog.installation import OmniRouteInstaller
from agentos.provider_catalog.claude_code import (
    ANTHROPIC_CLOUD_BASE_URL,
    DEFAULT_CLAUDE_CLI_BRIDGE_URL,
    ProviderTransportError,
    is_claude_cli_bridge_url,
    normalize_anthropic_base_url,
)
from agentos.provider_catalog.http import AnthropicModelCatalogClient


def _public(row) -> dict[str, object]:
    return {
        "provider": str(row["provider"]),
        "enabled": bool(row["enabled"]),
        "secret_ref": str(row["secret_ref"]),
        "has_secret": bool(row.get("has_secret")),
        "key_cooldown_seconds": int(row["key_cooldown_seconds"]),
        "catalog_refreshed_at": row.get("catalog_refreshed_at"),
        **({"base_url": row["base_url"]} if str(row["provider"]) in PROVIDERS_WITH_BASE_URL and row.get("base_url") else {}),
    }


class PostgresProviderConfigurationAdapter:
    """Production adapter for the ``ProviderConfigurationApplication`` port (frontend Fase D)."""

    def __init__(
        self,
        engine: Engine,
        *,
        cipher: ProviderSecretCipher | None = None,
        installer: OmniRouteInstaller | None = None,
        keys: PostgresProviderApiKeyAdapter | None = None,
        key_pool: PostgresProviderApiKeyAdapter | None = None,
        nvidia_client: NvidiaModelCatalogClient | None = None,
    ) -> None:
        self._engine = engine
        self._cipher = cipher or ProviderSecretCipher.from_environment()
        self._installer = installer or OmniRouteInstaller()
        resolved_keys = key_pool or keys or PostgresProviderApiKeyAdapter(engine, cipher=self._cipher)
        self._keys = resolved_keys
        self._key_pool = resolved_keys
        self._nvidia_client = nvidia_client or NvidiaModelCatalogClient()

    def configure(self, command: dict[str, object]) -> dict[str, object]:
        provider = str(command["provider"]).strip().lower()
        user_id = str(command["user_id"])
        enabled = bool(command["enabled"])
        existing_keys = self._key_pool.list_keys({"user_id": user_id, "provider": provider})
        base_url = _base_url(provider, command.get("base_url"), command.get("api_key"), has_existing_keys=bool(existing_keys))
        api_key = _api_key(provider, command.get("api_key"), base_url, has_existing_keys=bool(existing_keys))
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
                        base_url=base_url, secret_ref=secret_ref, key_cooldown_seconds=60,
                        catalog_refreshed_at=None, created_at=now, updated_at=now,
                    )
                )
                row = {
                    "provider": provider,
                    "enabled": enabled,
                    "base_url": base_url,
                    "secret_ref": secret_ref,
                    "has_secret": bool(api_key or existing_keys),
                    "key_cooldown_seconds": 60,
                    "catalog_refreshed_at": None,
                }
            else:
                connection.execute(
                    update(provider_configurations).where(provider_configurations.c.id == existing["id"]).values(
                        enabled=enabled, base_url=base_url, catalog_refreshed_at=None, updated_at=now,
                    )
                )
                row = {
                    "provider": provider,
                    "enabled": enabled,
                    "base_url": base_url,
                    "secret_ref": existing["secret_ref"],
                    "has_secret": bool(api_key or existing_keys),
                    "key_cooldown_seconds": existing["key_cooldown_seconds"],
                    "catalog_refreshed_at": None,
                }
            # Sharing this connection/transaction with the key write below
            # means the provider_configurations row and its principal key
            # commit -- or fail -- together, instead of a crash between two
            # separate transactions leaving a provider that reads as
            # configured with no key behind it.
            if api_key:
                self._key_pool.set_primary_key({"provider": provider, "user_id": user_id, "api_key": api_key}, connection=connection)
            elif not existing_keys:
                self._key_pool.clear_primary_key({"provider": provider, "user_id": user_id}, connection=connection)
        return _public(row)

    def set_key_cooldown_seconds(self, command: dict[str, object]) -> dict[str, object]:
        provider = str(command["provider"]).strip().lower()
        user_id = str(command["user_id"])
        seconds = int(command["seconds"])
        if seconds < 1:
            raise ValueError("key cooldown must be at least one second")
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
                    key_cooldown_seconds=seconds, updated_at=datetime.now(UTC),
                )
            )
        has_secret = bool(self._key_pool.list_keys({"user_id": user_id, "provider": provider}))
        return _public({**existing, "has_secret": has_secret, "key_cooldown_seconds": seconds})

    def inspect(self, query: dict[str, object]) -> dict[str, object]:
        provider = str(query["provider"]).strip().lower()
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
        has_secret = bool(self._key_pool.list_keys({"user_id": user_id, "provider": provider}))
        return _public({**row, "has_secret": has_secret})

    def revoke(self, command: dict[str, object]) -> dict[str, object]:
        provider = str(command["provider"]).strip().lower()
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
        has_secret = bool(self._key_pool.list_keys({"user_id": user_id, "provider": provider}))
        return _public({**existing, "has_secret": has_secret, "enabled": False})

    def test_connection(self, command: dict[str, object]) -> dict[str, object]:
        provider = str(command.get("provider") or "").strip().lower()
        if provider not in PROVIDERS_WITH_BASE_URL:
            raise ValueError("connection testing is not available for this provider")
        base_url = _base_url(provider, command.get("base_url"), command.get("api_key"))
        if provider == "nvidia":
            api_key = str(command.get("api_key") or "").strip()
            if not api_key and command.get("user_id"):
                cred = self._key_pool.next_available_key(str(command["user_id"]), "nvidia")
                if cred is not None:
                    api_key = cred.plaintext
            try:
                payload = self._nvidia_client.fetch_models(api_key, base_url=str(base_url))
            except NvidiaAuthenticationError:
                return {"reachable": True, "model_count": 0, "authenticated": False}
            except Exception as error:
                raise ValueError("provider connection failed") from error
            data = payload.get("data") if isinstance(payload, dict) else []
            model_count = len(data) if isinstance(data, list) else 0
            return {"reachable": True, "model_count": model_count, "authenticated": True}
        if provider == "omniroute":
            client: object = OmniRouteCatalogClient()
        elif provider == "opencode":
            client = OpenCodeModelCatalogClient()
        elif provider == "anthropic":
            client = AnthropicModelCatalogClient()
            base_url = base_url or (ANTHROPIC_CLOUD_BASE_URL if str(command.get("api_key") or "").strip() else DEFAULT_CLAUDE_CLI_BRIDGE_URL)
        else:
            client = OllamaCatalogClient()
        try:
            models = client.fetch(str(command["api_key"]), base_url=str(base_url))  # type: ignore[attr-defined]
            if provider == "ollama" and is_ollama_cloud(str(base_url)):
                if not models:
                    raise RuntimeError("Ollama Cloud returned no models")
                last_model_error: OllamaCloudAuthenticationError | None = None
                for model in models:
                    try:
                        client.verify_cloud_access(str(command["api_key"]), base_url=str(base_url), model=str(model["id"]))
                    except (OllamaCloudAuthenticationError, ProviderTransportError) as error:
                        # The Cloud catalog can contain models that are not
                        # enabled for this account. A single forbidden model
                        # must not make a valid credential look rejected.
                        last_model_error = error
                        continue
                    break
                else:
                    if last_model_error is not None:
                        raise last_model_error
                    raise RuntimeError("Ollama Cloud returned no usable models")
        except (OllamaCloudAuthenticationError, ProviderTransportError) as error:
            raise ProviderCredentialRejectedError from error
        except RuntimeError as error:
            # The adapter only permits the gateway's safe generic response.
            raise ValueError("provider connection failed") from error
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


def _base_url(provider: str, value: object, api_key: object = None, *, has_existing_keys: bool = False) -> str | None:
    if provider == "omniroute":
        return normalize_omniroute_base_url(str(value or DEFAULT_OMNIROUTE_BASE_URL))
    if provider == "ollama":
        return normalize_ollama_base_url(str(value or DEFAULT_OLLAMA_BASE_URL))
    if provider == "opencode":
        if isinstance(value, str) and value.strip():
            return normalize_opencode_base_url(value)
        default_opencode = (
            OPENCODE_CLOUD_BASE_URL
            if str(api_key or "").strip() or has_existing_keys
            else DEFAULT_OPENCODE_LOCAL_BASE_URL
        )
        return normalize_opencode_base_url(default_opencode)
    if provider == "nvidia":
        return normalize_nvidia_base_url(str(value or DEFAULT_NVIDIA_BASE_URL))
    if provider == "anthropic":
        if isinstance(value, str) and value.strip():
            normalized = normalize_anthropic_base_url(value)
            return None if normalized == ANTHROPIC_CLOUD_BASE_URL else normalized
        return None
    return None


def _api_key(provider: str, value: object, base_url: str | None = None, *, has_existing_keys: bool = False) -> str:
    api_key = str(value or "").strip()
    if api_key and len(api_key) < 4:
        raise ValueError("provider API key is required")
    optional = provider in PROVIDERS_WITH_OPTIONAL_KEY
    # Local Ollama, Local OpenCode Bridge, and Self-Hosted NVIDIA NIM need no
    # credential; their hosted cloud services always do.
    if provider == "ollama" and base_url is not None and is_ollama_cloud(base_url):
        optional = False
    if provider == "opencode" and base_url is not None and is_opencode_cloud(base_url):
        optional = False
    if provider == "nvidia" and base_url is not None and is_nvidia_cloud(base_url):
        optional = False
    if provider == "anthropic" and not (base_url is not None and is_claude_cli_bridge_url(base_url)):
        optional = False
    if not optional and not api_key and not has_existing_keys:
        raise ValueError("provider API key is required")
    return api_key
