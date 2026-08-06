from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from agentos.agents.models import (
    Agent,
    AgentAdministrativeState,
    AgentConfiguration,
    AgentPresentation,
    DataClassification,
    OpaqueAgentReference,
    PromptSpecification,
)
from agentos.agents.security import sanitize_public_error


def test_agent_requires_owner_user_and_valid_utc_timestamps(now, memory_scope, configuration_factory):
    with pytest.raises(ValueError):
        Agent(
            agent_id="agent:1",
            user_id="",
            workspace_id=None,
            owner="owner:1",
            display_name="Agent",
            administrative_state=AgentAdministrativeState.ACTIVE,
            current_config_version=1,
            private_memory_scope=memory_scope,
            created_by="actor:1",
            created_at=now,
            updated_at=now,
            suspended_at=None,
            archived_at=None,
            audit_refs=(),
        )
    with pytest.raises(ValueError):
        Agent(
            agent_id="agent:1",
            user_id="user:1",
            workspace_id=None,
            owner="owner:1",
            display_name="Agent",
            administrative_state=AgentAdministrativeState.ACTIVE,
            current_config_version=1,
            private_memory_scope=memory_scope,
            created_by="actor:1",
            created_at=datetime(2026, 8, 6),
            updated_at=now,
            suspended_at=None,
            archived_at=None,
            audit_refs=(),
        )


def test_configuration_is_immutable_and_versions_are_positive(configuration_factory):
    configuration = configuration_factory(config_version=1)
    with pytest.raises(ValueError):
        configuration_factory(config_version=0)
    with pytest.raises(FrozenInstanceError):
        configuration.config_version = 2


def test_configuration_requires_matching_superseded_version(configuration_factory):
    with pytest.raises(ValueError):
        configuration_factory(config_version=2, supersedes_version=None)
    with pytest.raises(ValueError):
        configuration_factory(config_version=2, supersedes_version=2)


def test_archived_agent_cannot_be_reactivated(now, memory_scope):
    archived = Agent(
        agent_id="agent:1",
        user_id="user:1",
        workspace_id=None,
        owner="owner:1",
        display_name="Agent",
        administrative_state=AgentAdministrativeState.ARCHIVED,
        current_config_version=1,
        private_memory_scope=memory_scope,
        created_by="actor:1",
        created_at=now,
        updated_at=now,
        suspended_at=None,
        archived_at=now,
        audit_refs=(),
    )
    with pytest.raises(ValueError):
        archived.transition_to(AgentAdministrativeState.ACTIVE, now=now)


def test_reference_and_public_error_reject_secrets():
    with pytest.raises(ValueError):
        OpaqueAgentReference("prompt:full-secret")
    with pytest.raises(ValueError) as error:
        sanitize_public_error("provider api_key=hidden")
    assert "hidden" not in str(error.value)


def test_classification_is_public_and_ordered():
    assert DataClassification.RESTRICTED.value == "RESTRICTED"


def test_configuration_only_accepts_opaque_references_and_safe_presentation(configuration_factory):
    with pytest.raises(ValueError):
        PromptSpecification("prompt:1", 1, DataClassification.INTERNAL)
    with pytest.raises(ValueError):
        AgentPresentation(None, "api_key=hidden")
