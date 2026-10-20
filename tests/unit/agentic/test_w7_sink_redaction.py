"""W7 (W0-R11): a token in a tool's command never reaches the activity log, its SSE stream or an HTTP hook."""
from __future__ import annotations

import json

import httpx

from agentos.agentic.events import AgentActivityEvent, AgentActivityEventType
from agentos.agentic.lifecycle import HookEvent
from agentos.hooks.config import parse_hook_mapping
from agentos.hooks.runners import HttpHookRunner

TOKEN = "ghp_0123456789abcdefghijABCDEF"


def _event(summary: str, payload: dict) -> AgentActivityEvent:
    from datetime import UTC, datetime

    fields = AgentActivityEvent.__dataclass_fields__
    values = {
        "event_id": "e1", "conversation_id": "c1", "turn_id": "t1", "execution_id": "x1", "user_id": "u1",
        "agent_id": "a1", "parent_agent_id": None,
        "event_type": AgentActivityEventType.TOOL_STARTED, "summary": summary, "payload": payload,
        "created_at": datetime.now(UTC), "sequence": 1,
    }
    return AgentActivityEvent(**{key: value for key, value in values.items() if key in fields})


def test_the_activity_event_redacts_a_token_in_the_command_and_summary() -> None:
    event = _event(f"Ran GITHUB_TOKEN={TOKEN} git push", {"command": f"GITHUB_TOKEN={TOKEN} git push", "label": "git push"})
    assert TOKEN not in event.summary and TOKEN not in json.dumps(dict(event.payload))
    assert event.payload["label"] == "git push"


def test_an_http_hook_receives_the_event_without_its_secrets() -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json={})

    specs, errors = parse_hook_mapping({"PreToolUse": [{"hooks": [{"type": "http", "url": "https://hooks.example.com/x"}]}]}, "user")
    assert not errors
    runner = HttpHookRunner(client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    runner.run(specs[0], {"hook_event_name": HookEvent.PRE_TOOL_USE.value, "tool_input": {"command": f"TOKEN={TOKEN} deploy"}})
    assert seen and TOKEN.encode() not in seen[0] and b"deploy" in seen[0]
