from dataclasses import replace

import pytest

from agentos.agents.models import AgentAdministrativeState, DataClassification
from agentos.agents.ports import AgentResolutionRejected, AgentResolutionRequest, AssignAgentWorkspace, ReconfigureAgent


def _request(*, user_id="user:1", workspace_id=None, version=None, purpose="execution.run", classification=DataClassification.INTERNAL):
    return AgentResolutionRequest(
        agent_id="agent:1",
        user_id=user_id,
        workspace_id=workspace_id,
        requested_config_version=version,
        purpose=purpose,
        correlation_id="correlation:resolve",
        classification=classification,
        actor="actor:1",
    )


def test_resolution_uses_current_or_explicit_authorized_version(agent_fixture, configuration_factory):
    agent_fixture.confirm_created()
    resolved = agent_fixture.registry.resolve_for_execution(_request())
    assert resolved.config_version == 1
    configuration = configuration_factory(config_version=2, supersedes_version=1)
    command = ReconfigureAgent(
        actor="actor:1", user_id="user:1", workspace_id=None, agent_id="agent:1",
        correlation_id="correlation:2", idempotency_key="reconfigure:resolve",
        requested_at=agent_fixture.now, expected_version=1, configuration=configuration,
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_reconfigure(command))
    old = agent_fixture.registry.resolve_for_execution(_request(version=1))
    current = agent_fixture.registry.resolve_for_execution(_request())
    assert old.config_version == 1
    assert current.config_version == 2
    assert resolved.config_version == 1


def test_resolution_denies_other_owner_suspended_archived_and_revoked_grant(agent_fixture):
    agent_fixture.confirm_created()
    with pytest.raises(AgentResolutionRejected):
        agent_fixture.registry.resolve_for_execution(_request(user_id="user:2"))
    agent_fixture.grant_policy.revoke(agent_fixture.configuration.tool_grants[0])
    with pytest.raises(AgentResolutionRejected):
        agent_fixture.registry.resolve_for_execution(_request())
    agent_fixture.grant_policy.revoked_refs.clear()
    agent_fixture.grant_policy.deny_purpose("execution.run")
    with pytest.raises(AgentResolutionRejected):
        agent_fixture.registry.resolve_for_execution(_request())
    agent_fixture.grant_policy.denied_purposes.clear()
    suspend = agent_fixture.administration.suspend_command(
        agent_id="agent:1", user_id="user:1", workspace_id=None, actor="actor:1",
        expected_version=1, idempotency_key="suspend:resolve",
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_suspend(suspend))
    with pytest.raises(AgentResolutionRejected):
        agent_fixture.registry.resolve_for_execution(_request())


def test_archived_agent_remains_readable_for_audit_but_not_resolvable(agent_fixture):
    agent_fixture.confirm_created()
    archive = agent_fixture.administration.archive_command(
        agent_id="agent:1", user_id="user:1", workspace_id=None, actor="actor:1",
        expected_version=1, idempotency_key="archive:resolution",
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_archive(archive))
    assert agent_fixture.registry.get("agent:1", agent_fixture.actor).agent.administrative_state is AgentAdministrativeState.ARCHIVED
    with pytest.raises(AgentResolutionRejected):
        agent_fixture.registry.resolve_for_execution(_request())


def test_user_scoped_agent_requires_explicit_workspace_assignment(agent_fixture):
    agent_fixture.confirm_created()
    with pytest.raises(AgentResolutionRejected):
        agent_fixture.registry.resolve_for_execution(_request(workspace_id="workspace:1"))
    command = AssignAgentWorkspace(
        actor="actor:1", user_id="user:1", workspace_id=None, agent_id="agent:1",
        correlation_id="correlation:assign", idempotency_key="assign:1",
        requested_at=agent_fixture.now, expected_version=1,
        assigned_workspace_id="workspace:1", assignment_ref=agent_fixture.configuration.model_profile_ref,
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_assign_workspace(command))
    resolved = agent_fixture.registry.resolve_for_execution(_request(workspace_id="workspace:1"))
    assert resolved.workspace_id == "workspace:1"
