from dataclasses import replace
from datetime import datetime, timezone

import pytest

from agentos.capabilities.models import CapabilityWaiting, CapabilityRunState, RunCapability
from agentos.capabilities.ports import ToolWaiting, StateConflict
from agentos.capabilities.service import CapabilityService
from .test_service_lifecycle import ctx, make_service


def test_tool_waiting_maps_to_waiting_tool_and_persists_bounded_checkpoint():
    service, control, persistence, state, _tool, _child, request = make_service(
        tool_result=ToolWaiting("invocation:1", "external tool pending")
    )
    accepted = service.start(request)
    from .test_security_limits_retry_compensation import activate

    outcome = activate(service, control, accepted)
    assert isinstance(outcome, CapabilityWaiting)
    assert outcome.reason.value == "TOOL"
    assert persistence.get(accepted.execution_id).state.value == "WAITING_TOOL"
    checkpoint = state.load_checkpoint(outcome.checkpoint_ref, ctx(accepted.execution_id))
    assert checkpoint.capability_run_id == accepted.capability_run_id
    assert "secret" not in repr(checkpoint).lower()
    assert "handle" not in repr(checkpoint).lower()
    assert any(event.event_type.value == "CapabilityCheckpointCreated" for event in state.events())
    event = state.events()[0]
    assert event.user_id == "user:1"
    assert event.execution_id == accepted.execution_id
    assert event.state_version >= 1


def test_state_port_rejects_stale_writer_and_scopes_checkpoint_load():
    service, _control, _persistence, state, _tool, _child, request = make_service()
    accepted = service.start(request)
    run = state.load(accepted.capability_run_id, ctx(accepted.execution_id))
    with pytest.raises(StateConflict):
        state.save(run, expected_version=run.state_version + 1)
