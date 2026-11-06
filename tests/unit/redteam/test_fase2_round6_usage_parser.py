"""Red team round 6: invented message_start id (N7)."""
from __future__ import annotations

import json
from typing import Any

from agentos.providers.claude_cli.stream import CliEventKind, StreamJsonParser


def _se(ev: dict[str, Any]) -> str:
    return json.dumps({"type": "stream_event", "event": ev})


def _init() -> str:
    return json.dumps({"type": "system", "subtype": "init", "session_id": "s", "model": "m", "apiKeySource": "none",
                       "mcp_servers": [{"name": "orin", "status": "connected"}], "tools": []})


def _assistant(mid: str | None, usage: dict[str, Any]) -> str:
    msg: dict[str, Any] = {"content": [{"type": "text", "text": "x"}], "usage": usage}
    if mid is not None:
        msg["id"] = mid
    return json.dumps({"type": "assistant", "message": msg})


def _result() -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "x", "num_turns": 1,
                       "duration_ms": 1, "session_id": "s", "usage": {"input_tokens": 1, "output_tokens": 1}})


def _usage(lines: list[str]) -> list[tuple[str | None, int, int]]:
    p = StreamJsonParser()
    got: list[tuple[str | None, int, int]] = []
    for line in [*lines]:
        for ev in p.feed_line(line):
            if ev.kind == CliEventKind.USAGE and ev.usage is not None:
                got.append((ev.message_id, ev.usage.context_tokens, ev.usage.output_tokens))
    for ev in p.flush_pending():
        if ev.kind == CliEventKind.USAGE and ev.usage is not None:
            got.append((ev.message_id, ev.usage.context_tokens, ev.usage.output_tokens))
    return got


U0 = {"input_tokens": 10, "cache_read_input_tokens": 1000, "output_tokens": 1}


def test_id_less_message_start_with_message_delta_before_the_assistant_packet_bills_once() -> None:
    """Fixture order (out-2): message_delta comes before the assistant packet. USAGE is emitted at message_delta under
    the invented id; the assistant packet with the real id then moves the bucket and bills it again."""
    lines = [_init(), _se({"type": "message_start", "message": {"usage": U0}}),
             _se({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 300}}),
             _assistant("A", {**U0, "output_tokens": 300}), _result()]
    got = _usage(lines)
    assert sum(c for _, c, _ in got) == 1010, got
    assert sum(o for _, _, o in got) == 300, got


def test_id_less_message_start_then_assistant_with_real_id_bills_once_with_final_output() -> None:
    lines = [_init(), _se({"type": "message_start", "message": {"usage": U0}}),
             _assistant("A", U0),
             _se({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 300}}),
             _result()]
    assert _usage(lines) == [("A", 1010, 300)]


def test_two_id_less_messages_in_a_row_bill_once_each() -> None:
    lines = [_init(),
             _se({"type": "message_start", "message": {"usage": U0}}), _assistant("A", U0),
             _se({"type": "message_delta", "delta": {}, "usage": {"output_tokens": 40}}),
             _se({"type": "message_start", "message": {"usage": {**U0, "cache_read_input_tokens": 1100}}}),
             _assistant("B", {**U0, "cache_read_input_tokens": 1100}),
             _se({"type": "message_delta", "delta": {}, "usage": {"output_tokens": 70}}), _result()]
    assert _usage(lines) == [("A", 1010, 40), ("B", 1110, 70)]
