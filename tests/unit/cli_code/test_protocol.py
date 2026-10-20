"""W6 Task 9: Orin's activity events become Claude Code's headless messages, as the official SDK parses them."""
from __future__ import annotations

import json

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, UserMessage
from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk.types import StreamEvent, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock

from agentos.cli_code.protocol import Translator, TurnUsage

MAIN = "agent:chat_x:main"


def _event(kind: str, agent: str = MAIN, **payload) -> dict:
    return {"event_type": kind, "agent_id": agent, "parent_agent_id": None, "payload": payload}


def _run(events: list[dict], *, partial: bool = False, state: str = "completed", error_code: str | None = None) -> list[dict]:
    translator = Translator(session_id="s-1", model="fake-coder:latest", partial=partial)
    out: list[dict] = []
    for event in events:
        out.extend(translator.feed(event))
    out.extend(translator.result(state=state, error_code=error_code, usage=TurnUsage(50, 13, 0.0), duration_ms=1200, duration_api_ms=900))
    for message in out:
        json.dumps(message)  # every message is plain JSON
    return out


def _parsed(messages: list[dict]) -> list[object]:
    return [parse_message(message) for message in messages]


TEXT_TURN = [_event("turn.started"), _event("assistant.delta", content="Hello "), _event("assistant.delta", content="from fake."), _event("turn.completed")]


def test_a_text_turn_is_one_assistant_message_and_a_result() -> None:
    parsed = _parsed(_run(TEXT_TURN))
    assert [type(item).__name__ for item in parsed] == ["AssistantMessage", "ResultMessage"]
    assistant, result = parsed
    assert isinstance(assistant, AssistantMessage) and assistant.model == "fake-coder:latest"
    assert assistant.content == [TextBlock(text="Hello from fake.")]
    assert isinstance(result, ResultMessage) and result.subtype == "success" and result.is_error is False
    assert result.result == "Hello from fake." and result.session_id == "s-1" and result.num_turns == 1
    assert result.usage["input_tokens"] == 50 and result.usage["output_tokens"] == 13
    assert result.model_usage["fake-coder:latest"]["outputTokens"] == 13


def test_partial_messages_follow_anthropics_stream_events() -> None:
    messages = _run(TEXT_TURN, partial=True)
    parsed = _parsed(messages)
    events = [item.event["type"] for item in parsed if isinstance(item, StreamEvent)]
    assert events == ["message_start", "content_block_start", "content_block_delta", "content_block_delta",
                      "content_block_stop", "message_delta", "message_stop"]
    deltas = [item.event["delta"]["text"] for item in parsed if isinstance(item, StreamEvent) and item.event["type"] == "content_block_delta"]
    assert deltas == ["Hello ", "from fake."]
    start = next(item for item in parsed if isinstance(item, StreamEvent))
    assistant = next(item for item in parsed if isinstance(item, AssistantMessage))
    assert start.event["message"]["id"] == assistant.message_id


def test_a_tool_round_is_tool_use_then_tool_result_then_a_new_model_call() -> None:
    events = [
        _event("assistant.delta", content="I will write it."),
        _event("tool.started", tool_name="write_file", invocation_id="tool-call:1", tool_input={"path": "a.txt", "content": "hi\n"}),
        _event("tool.finished", tool_name="write_file", invocation_id="tool-call:1", status="succeeded", tool_result="Wrote 3 bytes to a.txt."),
        _event("assistant.delta", content="Done."),
    ]
    parsed = _parsed(_run(events))
    kinds = [type(item).__name__ for item in parsed]
    assert kinds == ["AssistantMessage", "AssistantMessage", "UserMessage", "AssistantMessage", "ResultMessage"]
    text, tool_use, tool_result, done, result = parsed
    use = tool_use.content[0]
    assert isinstance(use, ToolUseBlock) and use.name == "Write" and use.input == {"file_path": "a.txt", "content": "hi\n"}
    assert text.message_id == tool_use.message_id != done.message_id
    block = tool_result.content[0]
    assert isinstance(block, ToolResultBlock) and block.tool_use_id == use.id and block.is_error is False
    assert block.content == "Wrote 3 bytes to a.txt."
    assert result.result == "Done." and result.num_turns == 2


def test_the_same_invocation_id_in_a_later_model_call_gets_a_new_tool_use_id() -> None:
    # Ollama numbers its calls per response ("tool-call:1" every time): the host still sees distinct ids.
    round_trip = [
        _event("tool.started", tool_name="read_file", invocation_id="tool-call:1", tool_input={"path": "a"}),
        _event("tool.finished", tool_name="read_file", invocation_id="tool-call:1", status="succeeded", tool_result="a"),
    ]
    parsed = _parsed(_run(round_trip + round_trip))
    ids = [item.content[0].id for item in parsed if isinstance(item, AssistantMessage)]
    results = [item.content[0].tool_use_id for item in parsed if isinstance(item, UserMessage)]
    assert len(set(ids)) == 2 and results == ids


def test_partial_tool_use_streams_its_input_as_json() -> None:
    events = [_event("tool.started", tool_name="run_command", invocation_id="c1", tool_input={"command": "ls"})]
    parsed = _parsed(_run(events, partial=True))
    stream = [item.event for item in parsed if isinstance(item, StreamEvent)]
    start = next(event for event in stream if event["type"] == "content_block_start")
    delta = next(event for event in stream if event["type"] == "content_block_delta")
    assert start["content_block"]["type"] == "tool_use" and start["content_block"]["name"] == "Bash"
    assert json.loads(delta["delta"]["partial_json"]) == {"command": "ls"}


def test_thinking_becomes_a_thinking_block() -> None:
    parsed = _parsed(_run([_event("agent.thought_delta", delta="Considering."), _event("assistant.delta", content="Answer.")]))
    assert isinstance(parsed[0].content[0], ThinkingBlock) and parsed[0].content[0].thinking == "Considering."
    assert parsed[1].content == [TextBlock(text="Answer.")] and parsed[0].message_id == parsed[1].message_id


def test_a_subagents_events_stay_inside_its_agent_call() -> None:
    sub = "agent:chat_x:sub-1"
    parsed = _parsed(_run([_event("assistant.delta", agent=sub, content="inner"), _event("assistant.delta", content="outer")]))
    texts = [block.text for item in parsed if isinstance(item, AssistantMessage) for block in item.content]
    assert texts == ["outer"]


@pytest.mark.parametrize(("error_code", "subtype"), [
    ("MAX_TURNS", "error_max_turns"), ("MAX_BUDGET_USD", "error_max_budget_usd"), ("PROVIDER_ERROR", "error_during_execution"),
])
def test_failed_turns_map_to_claude_codes_result_subtypes(error_code: str, subtype: str) -> None:
    result = _parsed(_run([_event("assistant.delta", content="partial")], state="failed", error_code=error_code))[-1]
    assert result.subtype == subtype and result.is_error is True and result.errors == [error_code]


def test_a_compaction_is_a_compact_boundary() -> None:
    translator = Translator(session_id="s", model="m")
    message = translator.feed(_event("context.compacted", trigger="manual", used_tokens=9000))[0]
    parsed = parse_message(message)
    assert isinstance(parsed, SystemMessage) and parsed.subtype == "compact_boundary"
    assert message["compact_metadata"] == {"trigger": "manual", "pre_tokens": 9000}


def test_a_denied_tool_is_a_permission_denial() -> None:
    events = [
        _event("tool.started", tool_name="write_file", invocation_id="c1", tool_input={"path": "a"}),
        _event("tool.finished", tool_name="write_file", invocation_id="c1", status="failed", error_code="CODE_PERMISSION_DENIED", tool_result="denied"),
    ]
    result = _parsed(_run(events))[-1]
    assert result.permission_denials and result.permission_denials[0]["tool_name"] == "Write"


def test_the_init_message() -> None:
    translator = Translator(session_id="s", model="ollama/m")
    message = translator.init_message(cwd="C:/w", tools=["Read", "Bash"], permission_mode="acceptEdits", mcp_servers=[],
                                      slash_commands=["compact"], version="0.9.3")
    parsed = parse_message(message)
    assert isinstance(parsed, SystemMessage) and parsed.subtype == "init"
    assert parsed.data["session_id"] == "s" and parsed.data["tools"] == ["Read", "Bash"] and parsed.data["model"] == "ollama/m"


def test_a_result_with_no_call_the_host_saw_is_ignored() -> None:
    # Orin's own bookkeeping (it settles the todo list at the end of a turn) is not a tool the model called.
    parsed = _parsed(_run([_event("tool.finished", tool_name="todo_write", invocation_id="auto_todo_settle_t", status="succeeded")]))
    assert [type(item).__name__ for item in parsed] == ["ResultMessage"]
