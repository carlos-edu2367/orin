from dataclasses import replace

import pytest

from agentos.agents.models import AgentAdministrativeState
from agentos.agents.ports import (
    AgentAccessDenied,
    AgentAccessContext,
    AgentNotFound,
    AuthorizedAgentQuery,
)


def test_get_does_not_reveal_agent_to_another_owner(agent_fixture):
    agent_fixture.confirm_created()
    other = AgentAccessContext(user_id="user:2", workspace_id=None, actor="actor:2")
    with pytest.raises(AgentNotFound):
        agent_fixture.registry.get("agent:1", other)
    wrong_actor = AgentAccessContext(user_id="user:1", workspace_id=None, actor="actor:2")
    with pytest.raises(AgentNotFound):
        agent_fixture.registry.get("agent:1", wrong_actor)


def test_list_is_scoped_and_paginated(agent_fixture):
    agent_fixture.confirm_created()
    first = agent_fixture.registry.list(replace(agent_fixture.query, limit=1))
    assert len(first.items) == 1
    assert first.next_cursor is None
    other = AuthorizedAgentQuery(
        user_id="user:2", workspace_id=None, actor="actor:2", purpose="agent.read"
    )
    assert agent_fixture.registry.list(other).items == ()


def test_state_transition_is_requested_through_execution_gate(agent_fixture):
    agent_fixture.confirm_created()
    command = agent_fixture.administration.suspend_command(
        agent_id="agent:1", user_id="user:1", workspace_id=None,
        actor="actor:1", expected_version=1,
    )
    reference = agent_fixture.administration.request_suspend(command)
    assert agent_fixture.registry.get("agent:1", agent_fixture.actor).agent.administrative_state is AgentAdministrativeState.ACTIVE
    agent_fixture.execution_gate.confirm(reference)
    assert agent_fixture.registry.get("agent:1", agent_fixture.actor).agent.administrative_state is AgentAdministrativeState.SUSPENDED
