from datetime import UTC, datetime, timedelta

import pytest

from agentos.agentic.events import AgentActivityEvent, AgentActivityEventType
from agentos.agentic.models import AgentActionKind, AgentActionRequest


def _event(**overrides):
    values = {
        "event_id": "activity:1",
        "conversation_id": "conversation:1",
        "turn_id": "turn:1",
        "execution_id": "execution:1",
        "user_id": "user:1",
        "agent_id": "agent:1",
        "event_type": AgentActivityEventType.TURN_STARTED,
        "sequence": 1,
        "summary": "Turn started",
        "payload": {"execution_ref": "execution:1"},
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    return AgentActivityEvent(**values)


def test_activity_event_rejects_event_types_outside_the_closed_contract():
    with pytest.raises(ValueError, match="event_type"):
        _event(event_type="model.secret_dumped")


def test_activity_event_redacts_raw_prompts_arguments_outputs_and_secrets():
    event = _event(
        payload={
            "prompt": "raw user prompt",
            "raw_args": {"api_key": "top-secret", "path": "src/app.py"},
            "output": "raw provider output",
            "result_ref": "artifact:1",
            "status": "succeeded",
        }
    )

    payload = dict(event.payload)
    assert payload["prompt"] == "[REDACTED]"
    assert payload["raw_args"] == "[REDACTED]"
    assert payload["output"] == "[REDACTED]"
    assert payload["result_ref"] == "artifact:1"
    assert "raw user prompt" not in repr(event)
    assert "top-secret" not in repr(event)


def test_activity_event_redacts_secret_like_summary_fragments():
    event = _event(summary="tool_args=hidden provider_output=private apiKey=secret")
    assert "hidden" not in event.summary
    assert "private" not in event.summary
    assert "secret" not in event.summary
    assert "[REDACTED]" in event.summary


def test_activity_event_enforces_bounded_public_text():
    with pytest.raises(ValueError, match="summary"):
        _event(summary="x" * 513)

    with pytest.raises(ValueError, match="payload"):
        _event(payload={"summary": "x" * 513})


def test_activity_event_accepts_the_bounded_structured_question_form():
    payload = {
        "tool_name": "ask_user",
        "tool_kind": "user_input",
        "status": "succeeded",
        "invocation_id": "call:questions",
        "questions": [
            {
                "id": f"choice_{index}",
                "question": f"Qual opcao voce prefere {index}?",
                "mode": "checkbox",
                "options": [{"id": f"option_{option}", "label": f"Opcao {option}"} for option in range(12)],
                "placeholder": "Observacao opcional",
            }
            for index in range(8)
        ],
    }

    event = _event(payload=payload)

    assert len(event.payload["questions"]) == 8
    assert len(event.payload["questions"][0]["options"]) == 12


def test_action_request_requires_exactly_one_typed_target_and_bounded_input():
    request = AgentActionRequest(
        action_id="action:1",
        turn_id="turn:1",
        agent_id="agent:1",
        kind=AgentActionKind.TOOL,
        tool_ref="tool:filesystem.read:v1",
        input={"path_ref": "workspace:src/app.py"},
        deadline=datetime.now(UTC) + timedelta(seconds=5),
        policy_context={"policy_ref": "policy:default"},
        idempotency_key="idem:1",
    )
    assert request.tool_ref == "tool:filesystem.read:v1"
    assert request.delegation_ref is None

    with pytest.raises(ValueError, match="exactly one"):
        AgentActionRequest(
            action_id="action:2",
            turn_id="turn:1",
            agent_id="agent:1",
            kind=AgentActionKind.TOOL,
            tool_ref="tool:one:v1",
            delegation_ref="delegation:one",
            input={},
            deadline=datetime.now(UTC) + timedelta(seconds=5),
            policy_context={},
            idempotency_key="idem:2",
        )
