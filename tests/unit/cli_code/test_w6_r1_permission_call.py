"""W6-R1: the host is asked about the call the broker is asking about, and fails closed otherwise."""
from __future__ import annotations

from types import SimpleNamespace

from agentos.cli_code.host_bridge import HostBridge
from agentos.cli_code.protocol import Translator

MAIN = {"agent_id": "agent:chat_1:main"}
SUB = {"agent_id": "agent:chat_1:sub_1", "parent_agent_id": "agent:chat_1:main"}


def _run(permission_prompt_tool: str = "stdio"):
    sent: list[dict] = []
    posted: list[tuple[str, dict]] = []
    run = SimpleNamespace(
        translator=Translator(session_id="s", model="m"),
        conversation_id="chat_1",
        # a host can answer only on stream-json (W6-R11); the real run refuses stdio otherwise
        options=SimpleNamespace(permission_prompt_tool=permission_prompt_tool, input_format="stream-json"),
        out=SimpleNamespace(message=sent.append),
        _debug=lambda line: None,
        client=SimpleNamespace(post=lambda path, body: posted.append((path, body))),
    )
    return run, sent, posted


def _started(run, agent: dict, invocation: str, tool: str, tool_input: dict) -> None:
    run.translator.feed({**agent, "event_type": "tool.started", "payload": {
        "tool_name": tool, "invocation_id": invocation, "tool_input": tool_input}})


def _decision_required(bridge: HostBridge, agent: dict, *, request_id: str, tool: str, invocation: str | None,
                       command: str = "", path: str = "") -> None:
    payload = {"permission_request_id": request_id, "tool_name": tool, "command": command, "path": path}
    if invocation is not None:
        payload["invocation_id"] = invocation
    bridge.on_turn_event({**agent, "event_type": "code_mode.decision_required", "payload": payload}, "turn_1")


def _echo_allow(bridge: HostBridge, sent: list[dict], request_index: int = -1, updated=None) -> None:
    message = sent[request_index]
    request = message["request"]
    bridge.answered({"subtype": "success", "request_id": message["request_id"],
                     "response": {"behavior": "allow", "updatedInput": request["input"] if updated is None else updated}})


def test_the_host_is_shown_the_call_the_permission_is_for_not_the_latest_one() -> None:
    run, sent, posted = _run()
    bridge = HostBridge(run)
    # One model response with two Bash calls: both tool_started events come before either runs.
    _started(run, MAIN, "call_A", "run_command", {"command": "rm -rf ~/projects"})
    _started(run, MAIN, "call_B", "run_command", {"command": "ls"})
    _decision_required(bridge, MAIN, request_id="perm_1", tool="run_command", invocation="call_A",
                       command="rm -rf ~/projects")

    request = sent[-1]["request"]
    assert request["input"] == {"command": "rm -rf ~/projects"}
    assert request["tool_use_id"] == run.translator.tool_use_id_for("call_A")
    assert request["tool_use_id"] != run.translator.tool_use_id_for("call_B")

    # A host that echoes the input of the second call back (allow with another input) is refused.
    _echo_allow(bridge, sent, updated={"command": "ls"})
    assert posted[-1][1]["decision"] == "deny"
    assert "changed" in posted[-1][1]["reason"]


def test_an_echo_of_the_shown_input_is_allowed_for_the_matched_call() -> None:
    run, sent, posted = _run()
    bridge = HostBridge(run)
    _started(run, MAIN, "call_A", "run_command", {"command": "rm -rf ~/projects"})
    _started(run, MAIN, "call_B", "run_command", {"command": "ls"})
    _decision_required(bridge, MAIN, request_id="perm_1", tool="run_command", invocation="call_A",
                       command="rm -rf ~/projects")
    _echo_allow(bridge, sent)
    assert posted[-1] == ("/v1/conversations/chat_1/turns/turn_1/tool-permission",
                          {"request_id": "perm_1", "decision": "allow_once"})


def test_a_subagents_permission_is_shown_the_broker_command_and_no_tool_use_id() -> None:
    run, sent, posted = _run()
    bridge = HostBridge(run)
    # The subagent's call is not open in the translator (its events are not fed to it).
    _decision_required(bridge, SUB, request_id="perm_2", tool="run_command", invocation="sub_call_9",
                       command="rm -rf ~/projects")
    request = sent[-1]["request"]
    assert request["input"] == {"command": "rm -rf ~/projects"}
    assert request["tool_use_id"] is None
    _echo_allow(bridge, sent)
    assert posted[-1][1]["decision"] == "allow_once"


def test_a_permission_without_an_invocation_id_uses_the_broker_fields() -> None:
    run, sent, _posted = _run()
    bridge = HostBridge(run)
    _started(run, MAIN, "call_A", "run_command", {"command": "ls"})
    _decision_required(bridge, MAIN, request_id="perm_3", tool="run_command", invocation=None, command="rm -rf x")
    assert sent[-1]["request"]["input"] == {"command": "rm -rf x"}
    assert sent[-1]["request"]["tool_use_id"] is None


def test_a_path_permission_is_shown_as_the_file_path() -> None:
    run, sent, _posted = _run()
    bridge = HostBridge(run)
    _decision_required(bridge, SUB, request_id="perm_4", tool="write_file", invocation="sub_call_1", path="src/app.py")
    assert sent[-1]["request"]["input"] == {"file_path": "src/app.py"}


def test_a_permission_with_neither_command_nor_path_is_denied_without_asking_the_host() -> None:
    run, sent, posted = _run()
    bridge = HostBridge(run)
    _decision_required(bridge, SUB, request_id="perm_5", tool="run_command", invocation="sub_call_2")
    assert sent == []
    assert posted[-1][1]["decision"] == "deny"
    assert "cannot show this call" in posted[-1][1]["reason"]


def test_the_input_is_never_empty_when_the_host_is_asked() -> None:
    run, sent, _posted = _run()
    bridge = HostBridge(run)
    _decision_required(bridge, SUB, request_id="perm_6", tool="run_command", invocation="sub_call_3", command="ls")
    assert sent[-1]["request"]["input"] != {}
