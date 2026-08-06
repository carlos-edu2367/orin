from dataclasses import replace

import pytest

from agentos.agents.models import AgentAdministrativeState
from agentos.agents.ports import AgentCommandRejected, AgentIdempotencyConflict, AgentVersionConflict, ReconfigureAgent


def test_creation_requires_execution_request_and_only_commits_on_confirmation(agent_fixture):
    reference = agent_fixture.administration.request_create(agent_fixture.create_command)
    assert agent_fixture.registry.list(agent_fixture.query).items == ()
    agent_fixture.execution_gate.confirm(reference)
    snapshot = agent_fixture.registry.get("agent:1", agent_fixture.actor)
    assert snapshot.agent_id == "agent:1"


def test_identical_command_is_idempotent_and_different_payload_conflicts(agent_fixture):
    first = agent_fixture.administration.request_create(agent_fixture.create_command)
    second = agent_fixture.administration.request_create(agent_fixture.create_command)
    assert first == second
    conflicting = replace(agent_fixture.create_command, display_name="Different")
    with pytest.raises(AgentIdempotencyConflict):
        agent_fixture.administration.request_create(conflicting)


def test_failed_or_cancelled_preconfirmation_leaves_no_partial_agent(agent_fixture):
    reference = agent_fixture.administration.request_create(agent_fixture.create_command)
    agent_fixture.execution_gate.cancel(reference)
    assert agent_fixture.registry.list(agent_fixture.query).items == ()


def test_confirmation_is_idempotent_after_the_fact(agent_fixture):
    reference = agent_fixture.administration.request_create(agent_fixture.create_command)
    first = agent_fixture.execution_gate.confirm(reference)
    second = agent_fixture.execution_gate.confirm(reference)
    assert first == second
    assert len(agent_fixture.persistence.confirmed_outbox()) == 1


def test_reconfiguration_creates_new_version_without_rewriting_old_snapshot(agent_fixture, configuration_factory):
    agent_fixture.confirm_created()
    configuration = configuration_factory(config_version=2, supersedes_version=1)
    command = ReconfigureAgent(
        actor="actor:1",
        user_id="user:1",
        workspace_id=None,
        agent_id="agent:1",
        correlation_id="correlation:2",
        idempotency_key="reconfigure:1",
        requested_at=agent_fixture.now,
        expected_version=1,
        configuration=configuration,
    )
    reference = agent_fixture.administration.request_reconfigure(command)
    agent_fixture.execution_gate.confirm(reference)
    current = agent_fixture.registry.get("agent:1", agent_fixture.actor)
    historical = agent_fixture.persistence.get_snapshot("agent:1", "user:1", None, 1)
    assert current.config_version == 2
    assert historical is not None and historical.config_version == 1


def test_reconfiguration_conflicts_when_expected_version_is_stale(agent_fixture, configuration_factory):
    agent_fixture.confirm_created()
    configuration = configuration_factory(config_version=2, supersedes_version=1)
    command = ReconfigureAgent(
        actor="actor:1", user_id="user:1", workspace_id=None, agent_id="agent:1",
        correlation_id="correlation:2", idempotency_key="reconfigure:stale",
        requested_at=agent_fixture.now, expected_version=2, configuration=configuration,
    )
    reference = agent_fixture.administration.request_reconfigure(command)
    with pytest.raises(AgentVersionConflict):
        agent_fixture.execution_gate.confirm(reference)


def test_suspend_resume_and_archive_follow_only_valid_transitions(agent_fixture):
    agent_fixture.confirm_created()
    suspend = agent_fixture.administration.suspend_command(
        agent_id="agent:1", user_id="user:1", workspace_id=None,
        actor="actor:1", expected_version=1, idempotency_key="suspend:state",
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_suspend(suspend))
    assert agent_fixture.registry.get("agent:1", agent_fixture.actor).agent.administrative_state is AgentAdministrativeState.SUSPENDED
    resume = agent_fixture.administration.resume_command(
        agent_id="agent:1", user_id="user:1", workspace_id=None,
        actor="actor:1", expected_version=1, idempotency_key="resume:state",
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_resume(resume))
    assert agent_fixture.registry.get("agent:1", agent_fixture.actor).agent.administrative_state is AgentAdministrativeState.ACTIVE
    archive = agent_fixture.administration.archive_command(
        agent_id="agent:1", user_id="user:1", workspace_id=None,
        actor="actor:1", expected_version=1, idempotency_key="archive:state",
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_archive(archive))
    assert agent_fixture.registry.get("agent:1", agent_fixture.actor).agent.administrative_state is AgentAdministrativeState.ARCHIVED
    resume_archived = agent_fixture.administration.resume_command(
        agent_id="agent:1", user_id="user:1", workspace_id=None, actor="actor:1",
        expected_version=1, idempotency_key="resume:archived",
    )
    with pytest.raises(AgentCommandRejected):
        agent_fixture.execution_gate.confirm(agent_fixture.administration.request_resume(resume_archived))


def test_cancel_after_confirmation_does_not_undo_confirmed_fact(agent_fixture):
    reference = agent_fixture.administration.request_create(agent_fixture.create_command)
    agent_fixture.execution_gate.confirm(reference)
    assert agent_fixture.execution_gate.cancel(reference).value == "CONFIRMED"
    assert agent_fixture.registry.get("agent:1", agent_fixture.actor).agent.administrative_state is AgentAdministrativeState.ACTIVE


def test_persistence_rejection_before_confirmation_creates_no_agent(agent_fixture):
    agent_fixture.persistence.reject_next()
    reference = agent_fixture.administration.request_create(agent_fixture.create_command)
    with pytest.raises(AgentCommandRejected):
        agent_fixture.execution_gate.confirm(reference)
    assert agent_fixture.registry.list(agent_fixture.query).items == ()
