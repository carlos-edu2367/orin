from dataclasses import replace

from agentos.agents.compat import (
    attach_config_version,
    to_context_operation_context,
    to_context_seed,
    to_provider_operation_context,
    to_provider_seed,
)


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
    enriched = attach_config_version(
        execution,
        resolved,
        correlation_id="correlation:1",
        purpose="execution.run",
    )
    assert enriched.agent_config_version == 1
    assert enriched.execution_id == execution.execution_id


def test_context_and_provider_operation_contexts_preserve_authorized_scope(agent_fixture):
    agent_fixture.confirm_created()
    resolved = agent_fixture.registry.resolve_for_execution(
        __import__("agentos.agents.ports", fromlist=["AgentResolutionRequest"]).AgentResolutionRequest(
            "agent:1", "user:1", None, None, "execution.run", "correlation:compat"
        )
    )
    context = to_context_operation_context(
        resolved, execution_id="execution:1", correlation_id="correlation:compat", purpose="execution.run"
    )
    provider = to_provider_operation_context(
        resolved,
        execution_id="execution:1",
        correlation_id="correlation:compat",
        purpose="execution.run",
        actor_ref="actor:1",
    )
    assert context.user_id == provider.user_id == "user:1"
    assert context.agent_id == provider.agent_id == "agent:1"
