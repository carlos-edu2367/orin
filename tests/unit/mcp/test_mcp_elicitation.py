"""W5: an MCP server's request for input is declined unless an Elicitation hook answers it."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.hooks.protocol import HookRun, decision_from_command, decision_from_json
from agentos.mcp.client import McpClient
from agentos.mcp.elicitation import answer_elicitation, checked_answer
from agentos.mcp.models import McpServerConfig, McpServerState, McpToolDescriptor, McpTransport
from agentos.mcp.toolset import McpToolProvider


def _bus(on_ask=None, on_result=None) -> tuple[LifecycleBus, list[dict]]:
    bus, seen = LifecycleBus(), []

    def subscriber(event):
        seen.append({"event": event.name, **dict(event.payload)})
        if event.name is HookEvent.ELICITATION and on_ask is not None:
            return on_ask
        if event.name is HookEvent.ELICITATION_RESULT and on_result is not None:
            return on_result
        return None

    bus.subscribe(subscriber)
    return bus, seen


def test_without_an_answering_hook_the_request_is_declined() -> None:
    assert answer_elicitation(None, "docs", {"message": "Name?"}) == ({"action": "decline"}, False)
    bus, seen = _bus()
    assert answer_elicitation(bus, "docs", {"message": "Name?", "requestedSchema": {"type": "object"}}) == ({"action": "decline"}, False)
    assert [item["event"] for item in seen] == [HookEvent.ELICITATION, HookEvent.ELICITATION_RESULT]
    assert seen[0]["mcp_server_name"] == "docs" and seen[0]["message"] == "Name?" and seen[1]["action"] == "decline"


def test_a_hook_answers_and_the_result_hook_may_change_it() -> None:
    accept = LifecycleDecision(elicitation={"action": "accept", "content": {"name": "Ada", "count": 2, "tags": ["a"]}})
    bus, seen = _bus(on_ask=accept)
    assert answer_elicitation(bus, "docs", {"message": "?"}) == ({"action": "accept", "content": {"name": "Ada", "count": 2, "tags": ["a"]}}, True)
    assert seen[1]["action"] == "accept" and seen[1]["content"] == {"name": "Ada", "count": 2, "tags": ["a"]}
    bus, _ = _bus(on_ask=accept, on_result=LifecycleDecision(blocked=True))
    assert answer_elicitation(bus, "docs", {"message": "?"})[0] == {"action": "decline"}


def test_an_answer_that_is_not_flat_is_declined() -> None:
    assert checked_answer({"action": "accept", "content": {"nested": {"a": 1}}}) == {"action": "decline"}
    assert checked_answer({"action": "accept"}) == {"action": "decline"}
    assert checked_answer({"action": "cancel", "content": {"a": 1}}) == {"action": "cancel"}
    assert checked_answer({"action": "maybe"}) == {"action": "decline"}


def test_hook_output_carries_the_answer() -> None:
    decision = decision_from_json(HookEvent.ELICITATION, {"hookSpecificOutput": {"hookEventName": "Elicitation", "action": "accept", "content": {"a": "1"}}})
    assert decision.elicitation == {"action": "accept", "content": {"a": "1"}}
    assert decision_from_json(HookEvent.ELICITATION_RESULT, {"hookSpecificOutput": {"action": "cancel"}}).elicitation == {"action": "cancel"}
    assert decision_from_command(HookEvent.ELICITATION, HookRun(2, "", "no", error=None)).blocked is True
    merged = LifecycleDecision(elicitation={"action": "accept", "content": {}})
    merged.merge(LifecycleDecision(elicitation={"action": "decline"}))
    assert merged.elicitation == {"action": "decline"}  # the more cautious answer wins


class _AskingServer:
    kind = "fake"

    def __init__(self) -> None:
        self.on_message = None
        self.answers: list[dict] = []

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def send(self, frame):
        if "id" not in frame:
            return None
        if frame["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "s"}, "capabilities": {}}
        else:
            self.answers.append(self.on_message({"jsonrpc": "2.0", "id": "e1", "method": "elicitation/create", "params": {"message": "Pick one"}}))
            result = {"content": [{"type": "text", "text": "done"}]}
        return {"jsonrpc": "2.0", "id": frame["id"], "result": result}


def test_the_provider_sends_a_servers_request_to_the_handler() -> None:
    server = _AskingServer()
    config = McpServerConfig(server_id="user:docs", user_id="u1", slug="docs", display_name="Docs", transport=McpTransport.HTTP,
                             url="https://mcp.example.com/mcp", state=McpServerState.ACTIVE, origin="user")
    provider = McpToolProvider([(config, (McpToolDescriptor("run", "r", {"type": "object"}),), {})],
                               client_factory=lambda config, secrets, **_: McpClient(server))
    asked: list[tuple[str, dict]] = []
    provider.elicitation_handler = lambda slug, params: asked.append((slug, dict(params))) or {"action": "cancel"}
    assert provider.definitions()[0].handler().status == "succeeded"
    assert asked == [("docs", {"message": "Pick one"})] and server.answers == [{"action": "cancel"}]


def test_the_session_declines_and_tells_the_model(tmp_path: Path) -> None:
    from agentos.agentic.reminders import ReminderQueue
    from tests.unit.agentic.test_disk_skills_session import _session

    session = _session(tmp_path, "hi")
    session._reminders = ReminderQueue()
    assert session._mcp_elicitation("docs", {"message": "Your API key?"}) == {"action": "decline"}
    texts = [item.text for item in session._reminders.drain() if item.kind == "mcp"]
    assert texts and "docs" in texts[0] and "Your API key?" in texts[0] and "declined" in texts[0]
