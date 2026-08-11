"""Versioned agent model configuration backed by authorized catalog records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from agentos.persistence.postgres.schema import agent_model_configurations
from agentos.providers.models import ModelSelection

from .models import ProviderCatalogContext


@dataclass(frozen=True, slots=True)
class AgentModelConfigurationRevision:
    agent_id: str
    config_version: int
    provider: str
    model_id: str
    model_profile_ref: str
    catalog_refreshed_at: datetime
    selection_ref: str | None = None
    model_revision: str | None = None


class AgentModelConfigurationService:
    def __init__(self, engine: Engine, catalog) -> None:
        self._engine = engine
        self._catalog = catalog

    def configure(self, context: ProviderCatalogContext, agent_id: str, config_version: int, provider: str, model_id: str, selection: ModelSelection | None = None) -> AgentModelConfigurationRevision:
        if config_version < 1:
            raise ValueError("config_version must be positive")
        record = next((item for item in self._catalog.list(context, provider) if item.model_id == model_id), None)
        if record is None:
            raise LookupError("model is not in the caller's authorized catalog")
        if selection is not None:
            selected = selection.primary
            if not str(selected.provider_ref).endswith(f":{provider}") or not str(selected.model_ref).endswith(f":{provider}:{model_id}"):
                raise ValueError("selection does not bind the requested provider model")
        profile_ref = f"model-profile:{provider}:{model_id}:{sha256(record.refreshed_at.isoformat().encode()).hexdigest()[:16]}"
        values = {
            "user_id": context.user_id, "agent_id": agent_id, "config_version": config_version,
            "provider": provider, "model_id": model_id, "model_profile_ref": profile_ref,
            "selection_ref": str(selection.selection_ref) if selection else None,
            "model_revision": str(selection.primary.descriptor_revision) if selection else None,
            "catalog_refreshed_at": record.refreshed_at, "created_at": datetime.now(UTC),
        }
        with self._engine.begin() as connection:
            existing = connection.execute(select(agent_model_configurations).where(
                agent_model_configurations.c.user_id == context.user_id,
                agent_model_configurations.c.agent_id == agent_id,
                agent_model_configurations.c.config_version == config_version,
            )).mappings().first()
            if existing is None:
                connection.execute(insert(agent_model_configurations).values(**values))
            elif any(existing[key] != values[key] for key in ("provider", "model_id", "model_profile_ref", "selection_ref", "model_revision")):
                raise ValueError("agent model configuration revision is immutable")
        return AgentModelConfigurationRevision(agent_id, config_version, provider, model_id, profile_ref, record.refreshed_at, values["selection_ref"], values["model_revision"])

    def get(self, context: ProviderCatalogContext, agent_id: str, config_version: int) -> AgentModelConfigurationRevision:
        with self._engine.connect() as connection:
            row = connection.execute(select(agent_model_configurations).where(
                agent_model_configurations.c.user_id == context.user_id,
                agent_model_configurations.c.agent_id == agent_id,
                agent_model_configurations.c.config_version == config_version,
            )).mappings().first()
        if row is None:
            raise LookupError("agent model configuration was not found")
        return AgentModelConfigurationRevision(str(row["agent_id"]), int(row["config_version"]), str(row["provider"]), str(row["model_id"]), str(row["model_profile_ref"]), row["catalog_refreshed_at"], row["selection_ref"], row["model_revision"])
