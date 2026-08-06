from dataclasses import replace

from agentos.agents.compat import attach_config_version, to_context_seed, to_provider_seed


def test_execution_can_carry_optional_agent_config_version_without_breaking_create(execution_factory):
    execution = execution_factory(agent_config_version=3)
    assert execution.agent_config_version == 3


def test_resolved_agent_seeds_context_and_provider_only_with_public_references(agent_fixture):
    agent_fixture.confirm_created()
    resolved = agent_fixture.registry.resolve_for_execution(
        __import__("agentos.agents.ports", fromlist=["AgentResolutionRequest"]).AgentResolutionRequest(
            "agent:1", "user:1", None, None, "execution.run", "correlation:compat"
        )
    )
    context_seed = to_context_seed(resolved)
    provider_seed = to_provider_seed(resolved)
    assert context_seed.agent_id == resolved.agent_id
    assert context_seed.config_version == resolved.config_version
    assert provider_seed.model_profile_ref == resolved.model_profile_ref
    assert "prompt_text" not in repr(provider_seed)


def test_snapshot_version_can_be_added_to_execution_without_rewriting_other_fields(execution_factory, agent_fixture):
    agent_fixture.confirm_created()
    resolved = agent_fixture.registry.resolve_for_execution(
        __import__("agentos.agents.ports", fromlist=["AgentResolutionRequest"]).AgentResolutionRequest(
            "agent:1", "user:1", None, None, "execution.run", "correlation:compat"
        )
    )
    execution = execution_factory()
    enriched = attach_config_version(execution, resolved)
    assert enriched.agent_config_version == 1
    assert enriched.execution_id == execution.execution_id
