"""Red team round 5: deferred USAGE in the stream-json parser (F4 fix)."""
from __future__ import annotations

import json
from typing import Any

from agentos.providers.claude_cli.stream import CliEventKind, StreamJsonParser


def _se(ev: dict[str, Any]) -> str:
    return json.dumps({"type": "stream_event", "event": ev})


def _init() -> str:
    return json.dumps({"type": "system", "subtype": "init", "session_id": "s", "model": "m", "apiKeySource": "none",
                       "mcp_servers": [{"name": "orin", "status": "connected"}], "tools": []})


def _assistant(mid: str | None, blocks: list[dict[str, Any]], usage: dict[str, Any]) -> str:
    msg: dict[str, Any] = {"content": blocks, "usage": usage}
    if mid is not None:
        msg["id"] = mid
    return json.dumps({"type": "assistant", "message": msg})


def _result(out: int) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "x", "num_turns": 1,
                       "duration_ms": 1, "session_id": "s", "usage": {"input_tokens": 1, "output_tokens": out}})


def _usage(lines: list[str]) -> list[tuple[str | None, int, int]]:
    p = StreamJsonParser()
    out: list[tuple[str | None, int, int]] = []
    for line in lines:
        for ev in p.feed_line(line):
            if ev.kind == CliEventKind.USAGE and ev.usage is not None:
                out.append((ev.message_id, ev.usage.context_tokens, ev.usage.output_tokens))
    for ev in p.flush_pending():
        if ev.kind == CliEventKind.USAGE and ev.usage is not None:
            out.append((ev.message_id, ev.usage.context_tokens, ev.usage.output_tokens))
    return out


U0 = {"input_tokens": 10, "cache_read_input_tokens": 1000, "output_tokens": 1}


def test_message_delta_before_the_assistant_packet_bills_once_with_final_output() -> None:
    lines = [_init(), _se({"type": "message_start", "message": {"id": "A", "usage": U0}}),
             _se({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 300}}),
             _assistant("A", [{"type": "text", "text": "x"}], {**U0, "output_tokens": 300}), _result(300)]
    assert _usage(lines) == [("A", 1010, 300)]


def test_two_streamed_messages_with_tool_round_bill_once_each() -> None:
    lines = [_init(),
             _se({"type": "message_start", "message": {"id": "A", "usage": U0}}),
             _assistant("A", [{"type": "tool_use", "id": "t1", "name": "mcp__orin__Read", "input": {}}], U0),
             _se({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 40}}),
             json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}),
             _se({"type": "message_start", "message": {"id": "B", "usage": {**U0, "cache_read_input_tokens": 1100}}}),
             _assistant("B", [{"type": "text", "text": "fim"}], {**U0, "cache_read_input_tokens": 1100}),
             _se({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 70}}),
             _result(110)]
    assert _usage(lines) == [("A", 1010, 40), ("B", 1110, 70)]


def test_streamed_message_cut_before_message_delta_is_billed_once_at_eof() -> None:
    lines = [_init(), _se({"type": "message_start", "message": {"id": "A", "usage": U0}}),
             _assistant("A", [{"type": "text", "text": "x"}], {**U0, "output_tokens": 9})]
    assert _usage(lines) == [("A", 1010, 9)]


def test_non_streamed_assistant_packet_is_billed_immediately() -> None:
    lines = [_init(), _assistant("Z", [{"type": "text", "text": "API Error"}], {"input_tokens": 0, "output_tokens": 0})]
    p = StreamJsonParser()
    kinds = [ev.kind for line in lines for ev in p.feed_line(line)]
    assert CliEventKind.USAGE in kinds


def test_message_start_without_id_does_not_bill_the_input_twice() -> None:
    """message_start lacks message.id -> the parser invents msg_<rand> and defers it; the assistant packets then
    carry the real id, billed at once (output_tokens 1); at the end the invented id is drained too."""
    lines = [_init(), _se({"type": "message_start", "message": {"usage": U0}}),
             _assistant("A", [{"type": "text", "text": "x"}], U0),
             _se({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 300}}),
             _result(300)]
    got = _usage(lines)
    assert sum(c for _, c, _ in got) == 1010, got
    assert sum(o for _, _, o in got) == 300, got
