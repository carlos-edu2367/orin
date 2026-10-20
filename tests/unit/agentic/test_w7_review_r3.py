"""W7-R3: the model's own words are not redacted in the activity stream, so a structured answer still parses."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from agentos.agentic.events import AgentActivityEvent, AgentActivityEventType


def _event(event_type: AgentActivityEventType, payload: dict) -> AgentActivityEvent:
    fields = AgentActivityEvent.__dataclass_fields__
    values = {
        "event_id": "e1", "conversation_id": "c1", "turn_id": "t1", "execution_id": "x1", "user_id": "u1",
        "agent_id": "agent:main", "parent_agent_id": None, "event_type": event_type, "summary": "Resposta em andamento",
        "payload": payload, "created_at": datetime.now(UTC), "sequence": 1,
    }
    return AgentActivityEvent(**{key: value for key, value in values.items() if key in fields})


def test_an_assistant_answer_keeps_an_author_flag_and_an_auth_word() -> None:
    text = "Rode git log --author=Carlos o campo auth: modo fica igual."
    event = _event(AgentActivityEventType.ASSISTANT_DELTA, {"content": text, "message_id": "m1"})
    assert event.payload["content"] == text


def test_a_json_schema_answer_still_parses_from_the_streamed_deltas() -> None:
    answer = {"author": "Carlos", "auth_mode": "oauth", "tokens": 3, "password_ok": False}
    chunks = [json.dumps(answer)[:20], json.dumps(answer)[20:]]
    streamed = "".join(
        _event(AgentActivityEventType.ASSISTANT_DELTA, {"content": chunk, "message_id": "m1"}).payload["content"]
        for chunk in chunks
    )
    assert json.loads(streamed) == answer


def test_a_thought_delta_keeps_its_text_too() -> None:
    text = "Vou checar --author=Carlos antes de responder"
    event = _event(AgentActivityEventType.AGENT_THOUGHT_DELTA, {"content": text, "delta": text, "state": "thinking_delta"})
    assert event.payload["content"] == text
    assert event.payload["delta"] == text


def test_a_secret_in_a_tool_payload_is_still_redacted() -> None:
    token = "ghp_0123456789abcdefghijABCDEF"
    event = _event(AgentActivityEventType.TOOL_STARTED, {"command": f"GITHUB_TOKEN={token} git push"})
    assert token not in event.payload["command"]
