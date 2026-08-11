from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from agentos.persistence.postgres import upgrade
from agentos.provider_catalog.agent_configuration import AgentModelConfigurationService
from agentos.provider_catalog.models import ProviderCatalogContext, ProviderModelRecord


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


class Catalog:
    def list(self, context, provider, favorites_only=False):
        return [ProviderModelRecord(provider, "anthropic/model-a", "Model A", 128000, (), ("text",), ("text",), None, datetime(2026, 8, 10, tzinfo=UTC))]


def test_agent_model_configuration_is_versioned_and_does_not_follow_later_catalog_changes() -> None:
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    service = AgentModelConfigurationService(engine, Catalog())
    context = ProviderCatalogContext("agent-config-owner", "agent.configure")

    revision = service.configure(context, "agent-configured", 1, "openrouter", "anthropic/model-a")

    assert revision.model_profile_ref.startswith("model-profile:openrouter:anthropic/model-a:")
    assert service.get(context, "agent-configured", 1) == revision
