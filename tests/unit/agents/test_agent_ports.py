from datetime import UTC, datetime

import pytest

from agentos.agents.models import AgentAdministrativeState
from agentos.agents.ports import (
    AgentPage,
    AgentPageCursor,
    AgentResolutionRequest,
    SuspendAgent,
)


def test_resolution_request_requires_purpose_and_correlation():
    with pytest.raises(ValueError):
        AgentResolutionRequest("agent:1", "user:1", None, None, "", "corr:1")
    with pytest.raises(ValueError):
        AgentResolutionRequest("agent:1", "user:1", None, None, "execute", "")


def test_admin_commands_carry_idempotency_and_expected_version():
    command = SuspendAgent(
        actor="actor:1",
        user_id="user:1",
        workspace_id=None,
        agent_id="agent:1",
        correlation_id="corr:1",
        idempotency_key="idem:1",
        requested_at=datetime.now(UTC),
        expected_version=1,
    )
    assert command.expected_version == 1
    assert command.target_state is AgentAdministrativeState.SUSPENDED


def test_agent_page_cursor_is_opaque_and_bounded():
    page = AgentPage(items=(), next_cursor=AgentPageCursor("cursor:1"))
    assert str(page.next_cursor) == "cursor:1"
    with pytest.raises(ValueError):
        AgentPageCursor("")
