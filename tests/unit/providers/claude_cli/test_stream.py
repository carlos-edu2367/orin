from __future__ import annotations

import json
from pathlib import Path

from agentos.providers.claude_cli.stream import (
    CliEventKind,
    CliUsage,
    StreamJsonParser,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIX2 = (FIXTURES_DIR / "out-2.jsonl").read_text(encoding="utf-8").splitlines()
FIX3 = (FIXTURES_DIR / "out-3.jsonl").read_text(encoding="utf-8").splitlines()
FIX5 = (FIXTURES_DIR / "out-5.jsonl").read_text(encoding="utf-8").splitlines()


def test_parser_emits_init_with_mcp_status() -> None:
    parser = StreamJsonParser()
    events = [e for line in FIX2 for e in parser.feed_line(line)]
    init_events = [e for e in events if e.kind == CliEventKind.INIT]
    assert len(init_events) == 1
    assert parser.init is not None
    assert parser.init.mcp_servers == {"orin": "connected"}
    assert parser.init.model == "claude-haiku-5-5"
    assert parser.init.api_key_source == "none"
    assert parser.init.tools == ("mcp__orin__Read",)


def test_parser_emits_tool_use_start_and_input_deltas_before_stop() -> None:
    parser = StreamJsonParser()
    events = [e for line in FIX3 for e in parser.feed_line(line)]
    kinds = [e.kind for e in events]
    assert (
        kinds.index(CliEventKind.TOOL_USE_START)
        < kinds.index(CliEventKind.TOOL_INPUT_DELTA)
        < kinds.index(CliEventKind.TOOL_USE_END)
    )
    end_ev = next(e for e in events if e.kind == CliEventKind.TOOL_USE_END)
    assert end_ev.tool_use_id == "toolu_01ABC"
    assert end_ev.tool_name == "mcp__orin__echo"
    assert end_ev.tool_input == {"text": "A"}
    assert end_ev.message_id == "msg_t1"


def test_parser_keeps_empty_thinking_deltas() -> None:
    parser = StreamJsonParser()
    events = [e for line in FIX2 for e in parser.feed_line(line)]
    thinking_events = [e for e in events if e.kind == CliEventKind.THINKING_DELTA]
    assert len(thinking_events) >= 1
    assert any(e.text == "" for e in thinking_events)
    sig_events = [e for e in events if e.kind == CliEventKind.THINKING_SIGNATURE]
    assert len(sig_events) == 1 and sig_events[0].text == "sig123"


def test_parser_reports_usage_once_per_message_id() -> None:
    parser = StreamJsonParser()
    assistant_line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": "msg_dup",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": "part1"}],
                "usage": {
                    "input_tokens": 50,
                    "output_tokens": 10,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 30,
                },
            },
        }
    )
    ev1 = parser.feed_line(assistant_line)
    ev2 = parser.feed_line(assistant_line)
    usage_events = [
        e for e in (*ev1, *ev2) if e.kind == CliEventKind.USAGE
    ]
    assert len(usage_events) == 1
    assert usage_events[0].message_id == "msg_dup"
    assert usage_events[0].usage == CliUsage(
        input_tokens=50,
        output_tokens=10,
        cache_read_input_tokens=200,
        cache_creation_input_tokens=30,
    )
    assert usage_events[0].usage.context_tokens == 280
    stream_usage = usage_events[0].usage.to_stream_usage()
    assert stream_usage.input_tokens == 280
    assert stream_usage.cached_input_tokens == 200
    assert stream_usage.cache_read_input_tokens == 200
    assert stream_usage.cache_creation_input_tokens == 30


def test_parser_tool_result_user_message_becomes_tool_result_event() -> None:
    parser = StreamJsonParser()
    events = [e for line in FIX3 for e in parser.feed_line(line)]
    tr_events = [e for e in events if e.kind == CliEventKind.TOOL_RESULT]
    assert len(tr_events) == 1
    assert tr_events[0].tool_use_id == "toolu_01ABC"
    assert "echo:A" in tr_events[0].text


def test_parser_ignores_garbage_and_non_object_lines() -> None:
    parser = StreamJsonParser()
    assert parser.feed_line("not json") == []
    assert parser.feed_line("[1]") == []
    assert parser.feed_line("   ") == []


def test_result_carries_api_error_status_and_model_usage() -> None:
    parser = StreamJsonParser()
    events = [e for line in FIX5 for e in parser.feed_line(line)]
    assert any(e.kind == CliEventKind.COMPACT_BOUNDARY for e in events)
    assert any(e.kind == CliEventKind.RATE_LIMIT for e in events)
    assert parser.rate_limit is not None
    assert parser.rate_limit.rate_limit_type == "five_hour"
    assert parser.result is not None
    assert parser.result.api_error_status == 404
    assert parser.result.is_error is True
    assert "claude-opus-5-5" in parser.result.model_usage



def test_parser_handles_malformed_block_indices_without_crashing() -> None:
    parser = StreamJsonParser()
    for bad_idx in (None, "abc", {}, -1, True):
        line = json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "index": bad_idx,
                    "content_block": {"type": "text", "text": "hi"},
                },
            }
        )
        parser.feed_line(line)
        delta_line = json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": bad_idx,
                    "delta": {"type": "text_delta", "text": "hi"},
                },
            }
        )
        parser.feed_line(delta_line)
        stop_line = json.dumps(
            {
                "type": "stream_event",
                "event": {"type": "content_block_stop", "index": bad_idx},
            }
        )
        parser.feed_line(stop_line)


def test_parser_keeps_multiple_text_blocks_in_single_assistant_packet_and_deduplicates_idless_tool() -> None:
    parser = StreamJsonParser()
    evs = parser.feed_line(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "id": "m_multi",
                    "content": [
                        {"type": "thinking", "thinking": "t1"},
                        {"type": "thinking", "thinking": "t2"},
                        {"type": "text", "text": "A"},
                        {"type": "text", "text": "B"},
                    ],
                },
            }
        )
    )
    assert [e.text for e in evs if e.kind == CliEventKind.THINKING_DELTA] == ["t1", "t2"]
    assert [e.text for e in evs if e.kind == CliEventKind.TEXT_DELTA] == ["A", "B"]

    # Tool without id in stream_event followed by assistant packet without id
    p2 = StreamJsonParser()
    out: list[Any] = []
    for pkt in (
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m_idless"}}},
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "name": "mcp__orin__Read", "input": {}},
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"path":"a"}'},
            },
        },
        {"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}},
        {
            "type": "assistant",
            "message": {
                "id": "m_idless",
                "content": [{"type": "tool_use", "name": "mcp__orin__Read", "input": {"path": "a"}}],
            },
        },
    ):
        out.extend(p2.feed_line(json.dumps(pkt)))
    starts = [e for e in out if e.kind == CliEventKind.TOOL_USE_START]
    ends = [e for e in out if e.kind == CliEventKind.TOOL_USE_END]
    assert len(starts) == 1 and len(ends) == 1
