"""Fail-closed production composition for the AgentOS HTTP boundary.

This module deliberately contains no in-memory fallback. Deployment code must
provide real security, application/query and event-stream adapters; readiness
also verifies the PostgreSQL and Redis dependencies before traffic is admitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import SecretStr, model_validator
from pydantic_settings import SettingsConfigDict

from sqlalchemy.engine import Engine

from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticationError
from agentos.configuration import AgentOSSettings
from agentos.persistence.postgres.event_stream import PostgresClientEventStream
from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter, ExecutionQueryAdapter
from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.security import PostgresSecurityService


class ProductionSettings(AgentOSSettings):
    """Flat, explicit deployment configuration; secrets remain redacted."""

    # This is the production specialization of AgentOSSettings. Flat aliases
    # are required for deployment tooling while the base model preserves the
    # typed local-secret and provider-catalog validation from RFC 604.
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    DATABASE_URL: str
    REDIS_URL: str
    AGENTOS_ENV: str = "development"
    OPENROUTER_ENABLED: bool = False
    OPENROUTER_API_KEY: SecretStr | None = None
    OPENROUTER_MODEL: str | None = None
    ANTHROPIC_ENABLED: bool = False
    ANTHROPIC_API_KEY: SecretStr | None = None
    ANTHROPIC_MODEL: str | None = None
    OPENAI_ENABLED: bool = False
    OPENAI_API_KEY: SecretStr | None = None
    OPENAI_MODEL: str | None = None

    @model_validator(mode="after")
    def _validate_enabled_providers(self) -> "ProductionSettings":
        for name in ("OPENROUTER", "ANTHROPIC", "OPENAI"):
            if getattr(self, f"{name}_ENABLED") and (
                getattr(self, f"{name}_API_KEY") is None or not getattr(self, f"{name}_MODEL")
            ):
                raise ValueError(f"enabled {name} provider requires API key and model")
        return self


@dataclass(frozen=True, slots=True)
class DependencyProbe:
    postgres: Callable[[], bool]
    redis: Callable[[], bool]

    @classmethod
    def from_settings(cls, settings: ProductionSettings) -> "DependencyProbe":
        def postgres_ready() -> bool:
            try:
                from sqlalchemy import create_engine, text
                engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                engine.dispose()
                return True
            except Exception:
                return False

        def redis_ready() -> bool:
            try:
                import redis
                client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
                return bool(client.ping())
            except Exception:
                return False

        return cls(postgres_ready, redis_ready)

    def ready(self) -> bool:
        try:
            return bool(self.postgres()) and bool(self.redis())
        except Exception:
            return False


class _UnavailableProductionSecurity:
    """Blocks all protected traffic until a real security adapter is composed."""

    def authenticate(self, *, bearer_token: str | None, session_id: str | None) -> object:
        raise AuthenticationError("production security adapter is unavailable")

    def validate_csrf(self, principal: object, token: str | None, origin: str | None) -> None:
        raise AuthenticationError("production security adapter is unavailable")

    def authorize(self, principal: object, *, action: str, resource_id: str | None, purpose: str) -> None:
        raise AuthenticationError("production security adapter is unavailable")

    def check_rate_limit(self, principal: object, *, action: str, origin: str | None) -> None:
        raise AuthenticationError("production security adapter is unavailable")

    def revocation_epoch(self, principal: object) -> int:
        raise AuthenticationError("production security adapter is unavailable")


class _UnavailableApplicationPort:
    def __getattr__(self, _: str) -> Callable[..., object]:
        def unavailable(*args: object, **kwargs: object) -> object:
            raise RuntimeError("production application adapter is unavailable")
        return unavailable


def unavailable_production_services() -> ApiServices:
    """A fail-closed composition, never an in-memory runtime fallback."""
    unavailable = _UnavailableApplicationPort()
    return ApiServices(
        security=_UnavailableProductionSecurity(),
        execution_application=unavailable,
        execution_query=unavailable,
        resource_services={name: unavailable for name in ("agents", "capabilities", "tools", "workspaces", "artifacts", "memories")},
        events=unavailable,  # type: ignore[arg-type]
    )


def compose_production_services(engine: Engine) -> ApiServices:
    """Durable composition for the frontend-facing surface (Fase 0/D).

    Executions and their public event stream are backed by the real
    PostgreSQL outbox/persistence; `resource_services` for agents,
    capabilities, tools, workspaces, artifacts and memories stay unavailable
    because Fase 0 does not establish a public DTO/permission for them (see
    docs/frontend/BACKEND_CAPABILITY_MATRIX.md). `provider_configuration` is
    backed by `PostgresProviderConfigurationAdapter` over a dedicated
    `provider_configurations` table (frontend Fase D; see docs/frontend/
    IMPLEMENTATION_PLAN.md, Fase D "Decisões locais").
    """
    unavailable = _UnavailableApplicationPort()
    return ApiServices(
        security=PostgresSecurityService(engine),
        execution_application=ExecutionApplicationAdapter(engine),
        execution_query=ExecutionQueryAdapter(engine),
        resource_services={name: unavailable for name in ("agents", "capabilities", "tools", "workspaces", "artifacts", "memories")},
        provider_configuration=PostgresProviderConfigurationAdapter(engine),
        events=PostgresClientEventStream(engine),  # type: ignore[arg-type]
    )


def create_production_app(settings: ProductionSettings, *, services: ApiServices | None = None, probe: DependencyProbe | None = None) -> FastAPI:
    """Create production HTTP surface with real ports supplied by composition.

    Supplying no services creates an explicitly unavailable application; it does
    not silently select any test adapter. The outer bootstrap must inject only
    durable/authorized implementations of the public application ports.
    """
    if services is not None and any(
        component.__class__.__name__.startswith("InMemory")
        or component.__class__.__module__.endswith(".in_memory")
        for component in (services.security, services.events)
    ):
        raise ValueError("production composition cannot use in-memory security or event adapters")
    app = create_app(services or unavailable_production_services())
    dependency_probe = probe or DependencyProbe.from_settings(settings)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        if dependency_probe.ready():
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "unavailable"}, status_code=503)

    return app
