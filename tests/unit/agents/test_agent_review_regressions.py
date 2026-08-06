from dataclasses import replace

import pytest

from agentos.agents.models import Agent, AgentAdministrativeState, OpaqueAgentReference, WorkspaceAssignment
from agentos.agents.ports import (
    AgentCommandRejected,
    AgentPageCursor,
    AgentVersionConflict,
    AssignAgentWorkspace,
    ReconfigureAgent,
)


def test_same_agent_id_cannot_be_created_in_another_workspace(agent_fixture):
    agent_fixture.confirm_created()
    command = replace(
        agent_fixture.create_command,
        workspace_id="workspace:other",
        idempotency_key="create:other-workspace",
    )
    reference = agent_fixture.administration.request_create(command)
    with pytest.raises(AgentCommandRejected):
        agent_fixture.execution_gate.confirm(reference)
    assert len(agent_fixture.persistence._agents) == 1
    assert agent_fixture.persistence._agents["agent:1"].workspace_id is None


def test_reconfiguration_requires_next_version_and_preserves_history(agent_fixture, configuration_factory):
    agent_fixture.confirm_created()
    jumped = configuration_factory(config_version=9, supersedes_version=8)
    command = ReconfigureAgent(
        actor="actor:1", user_id="user:1", workspace_id=None, agent_id="agent:1",
        correlation_id="correlation:jump", idempotency_key="reconfigure:jump",
        requested_at=agent_fixture.now, expected_version=1, configuration=jumped,
    )
    reference = agent_fixture.administration.request_reconfigure(command)
    with pytest.raises(AgentVersionConflict):
        agent_fixture.execution_gate.confirm(reference)
    assert agent_fixture.persistence.get_snapshot("agent:1", "user:1", None).config_version == 1
    assert agent_fixture.execution_gate.inspect(reference).status.value == "NOT_COMMITTED"

    valid = configuration_factory(config_version=2, supersedes_version=1)
    first = ReconfigureAgent(
        actor="actor:1", user_id="user:1", workspace_id=None, agent_id="agent:1",
        correlation_id="correlation:valid", idempotency_key="reconfigure:valid",
        requested_at=agent_fixture.now, expected_version=1, configuration=valid,
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_reconfigure(first))
    repeated = replace(first, idempotency_key="reconfigure:repeat", expected_version=2)
    reference = agent_fixture.administration.request_reconfigure(repeated)
    with pytest.raises(AgentVersionConflict):
        agent_fixture.execution_gate.confirm(reference)
    assert agent_fixture.persistence.get_snapshot("agent:1", "user:1", None, 2).configuration == valid


def test_unknown_commit_is_resolved_to_committed_only_after_authorized_inspection(agent_fixture):
    agent_fixture.persistence.indeterminate_next()
    reference = agent_fixture.administration.request_create(agent_fixture.create_command)
    first = agent_fixture.execution_gate.confirm(reference)
    assert first.receipt.commit_state.value == "UNKNOWN"
    resolved = agent_fixture.execution_gate.confirm(reference)
    assert resolved.receipt.commit_state.value == "COMMITTED"
    assert agent_fixture.execution_gate.inspect(reference).status.value == "CONFIRMED"
    with pytest.raises(LookupError):
        agent_fixture.persistence.inspect_commit(
            user_id="user:1", transaction_id=reference.execution_id, idempotency_key="wrong-key"
        )


def test_references_are_opaque_and_collection_fields_are_deeply_immutable(now, configuration_factory, memory_scope):
    grants = [OpaqueAgentReference("tool-grant:1")]
    configuration = configuration_factory(tool_grants=grants)
    grants.append(OpaqueAgentReference("tool-grant:2"))
    assert configuration.tool_grants == (OpaqueAgentReference("tool-grant:1"),)
    with pytest.raises(ValueError):
        WorkspaceAssignment("workspace:1", "raw-assignment", "actor:1", now)
    with pytest.raises(ValueError):
        configuration_factory(workspace_assignments=(("workspace:1", "raw-assignment"),))
    with pytest.raises(ValueError):
        AssignAgentWorkspace(
            actor="actor:1", user_id="user:1", workspace_id=None, agent_id="agent:1",
            correlation_id="correlation:raw", idempotency_key="assign:raw", requested_at=now,
            expected_version=1, assigned_workspace_id="workspace:1", assignment_ref="raw-assignment",
        )
    with pytest.raises(ValueError):
        Agent(
            agent_id="agent:1", user_id="user:1", workspace_id=None, owner="actor:1",
            display_name="Agent", administrative_state=AgentAdministrativeState.ACTIVE,
            current_config_version=1, private_memory_scope=memory_scope, created_by="actor:1",
            created_at=now, updated_at=now, suspended_at=None, archived_at=None,
            audit_refs=("raw-audit",),
        )


def test_page_cursor_is_bound_to_user_and_workspace(agent_fixture, configuration_factory, memory_scope):
    agent_fixture.confirm_created()
    second = replace(
        agent_fixture.create_command,
        agent_id="agent:2",
        idempotency_key="create:2",
        initial_configuration=configuration_factory(agent_id="agent:2"),
        private_memory_scope=replace(
            memory_scope,
            agent_id="agent:2",
            scope_ref=OpaqueAgentReference("memory-scope:agent-2"),
        ),
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_create(second))
    page = agent_fixture.registry.list(replace(agent_fixture.query, limit=1))
    assert page.next_cursor is not None
    with pytest.raises(ValueError):
        agent_fixture.registry.list(
            replace(agent_fixture.query, user_id="user:2", actor="actor:2", cursor=page.next_cursor)
        )
    with pytest.raises(ValueError):
        agent_fixture.registry.list(
            replace(agent_fixture.query, cursor=AgentPageCursor("agent-cursor:0"))
        )
