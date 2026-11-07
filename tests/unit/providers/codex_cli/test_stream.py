from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest

from agentos.providers.claude_cli.native_loop import find_forbidden_item_kind
from agentos.providers.cli_adapter import (
    CliEvent,
    CliEventKind,
    CliStreamTranslator,
    CliUsage,
)
from agentos.providers.codex_cli.stream import CodexJsonlTranslator

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture_lines(name: str) -> list[str]:
    path = FIXTURES_DIR / name
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _run_lines(
    translator: CodexJsonlTranslator, lines: Sequence[str]
) -> list[CliEvent]:
    events: list[CliEvent] = []
    for line in lines:
        events.extend(translator.feed_line(line))
    events.extend(translator.flush_pending())
    return events


def test_conforms_to_cli_stream_translator_protocol() -> None:
    tr = CodexJsonlTranslator(model="gpt-5.4")
    assert isinstance(tr.latest_usage(), CliUsage)
    assert tr.init is None
    assert tr.result is None
    assert tr.rate_limit is None
    assert tr.stop_reason is None
    # Structural conformance check
    _: CliStreamTranslator = tr


def test_simple_text_fixture_translation() -> None:
    tr = CodexJsonlTranslator(model="gpt-5.4-mini")
    events = _run_lines(tr, _load_fixture_lines("simple_text.jsonl"))

    text = "".join(
        ev.text for ev in events if ev.kind == CliEventKind.TEXT_DELTA
    )
    assert text == "PONG_CODEX"

    assert tr.init is not None
    assert tr.init.session_id == "01a1b331-e989-7f71-ae46-8e13f3d2e46f"
    assert tr.init.api_key_source == "none"
    assert tr.init.model == "gpt-5.4-mini"
    assert tr.init.tools == ()

    assert tr.result is not None
    assert tr.result.is_error is False
    assert tr.result.subtype == "success"
    assert tr.result.result_text == "PONG_CODEX"
    assert tr.result.session_id == "01a1b331-e989-7f71-ae46-8e13f3d2e46f"

    kinds = [ev.kind for ev in events]
    assert CliEventKind.INIT in kinds
    assert CliEventKind.TEXT_DELTA in kinds
    assert CliEventKind.ASSISTANT_MESSAGE in kinds
    assert CliEventKind.USAGE in kinds
    assert CliEventKind.RESULT in kinds


def test_tool_call_fixture_translation() -> None:
    tr = CodexJsonlTranslator()
    events = _run_lines(tr, _load_fixture_lines("tool_call.jsonl"))

    starts = [ev for ev in events if ev.kind == CliEventKind.TOOL_USE_START]
    ends = [ev for ev in events if ev.kind == CliEventKind.TOOL_USE_END]
    results = [ev for ev in events if ev.kind == CliEventKind.TOOL_RESULT]

    assert len(starts) == 1
    assert len(ends) == 1
    assert len(results) == 1

    assert starts[0].tool_use_id == "item_0"
    assert starts[0].tool_name == "echo"
    assert starts[0].tool_input == {"text": "hello-from-codex"}

    assert ends[0].tool_use_id == "item_0"
    assert ends[0].tool_name == "echo"
    assert ends[0].tool_input == {"text": "hello-from-codex"}

    assert results[0].tool_use_id == "item_0"
    assert results[0].tool_name == "echo"
    assert results[0].text == "ECHO_OK:hello-from-codex"

    kinds = [ev.kind for ev in events]
    assert kinds.index(CliEventKind.TOOL_USE_START) < kinds.index(
        CliEventKind.TOOL_USE_END
    )
    assert kinds.index(CliEventKind.TOOL_USE_END) < kinds.index(
        CliEventKind.TOOL_RESULT
    )


def test_turn_completed_token_usage_mapping_d5() -> None:
    tr = CodexJsonlTranslator()
    tr.feed_line('{"type":"thread.started","thread_id":"th_1"}')
    events = tr.feed_line(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 24763,
                    "cached_input_tokens": 24448,
                    "output_tokens": 120,
                },
            }
        )
    )
    usage_events = [ev for ev in events if ev.kind == CliEventKind.USAGE]
    assert len(usage_events) == 1
    usage = usage_events[0].usage
    assert usage is not None
    assert usage == CliUsage(
        input_tokens=315,
        output_tokens=120,
        cache_read_input_tokens=24448,
        cache_creation_input_tokens=0,
        reasoning_tokens=None,
    )
    assert usage.context_tokens == 24763
    assert tr.latest_usage() == usage

    # Verify reasoning_output_tokens and cache_write_input_tokens mapping
    tr2 = CodexJsonlTranslator()
    tr2.feed_line('{"type":"thread.started","thread_id":"th_2"}')
    events2 = tr2.feed_line(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 400,
                    "cache_write_input_tokens": 50,
                    "output_tokens": 200,
                    "reasoning_output_tokens": 75,
                },
            }
        )
    )
    usage2 = [ev for ev in events2 if ev.kind == CliEventKind.USAGE][0].usage
    assert usage2 is not None
    assert usage2.input_tokens == 600
    assert usage2.cache_read_input_tokens == 400
    assert usage2.cache_creation_input_tokens == 50
    assert usage2.output_tokens == 200
    assert usage2.reasoning_tokens == 75
    stream_usage = usage2.to_stream_usage()
    assert stream_usage.reasoning_tokens == 75
    assert stream_usage.input_tokens == 1050
    assert stream_usage.cached_input_tokens == 400


@pytest.mark.parametrize(
    "bad_line",
    [
        "",
        "   \t\n  ",
        "not-json {{{",
        "42",
        "[]",
        '"just-a-string"',
        "true",
        "null",
    ],
)
def test_malformed_lines_return_empty_without_raising(bad_line: str) -> None:
    tr = CodexJsonlTranslator()
    assert tr.feed_line(bad_line) == []


def test_error_and_turn_failed_packets_produce_error_result() -> None:
    tr_err = CodexJsonlTranslator()
    ev_err = tr_err.feed_line(
        '{"type": "error", "message": "fatal authentication failure"}'
    )
    assert len(ev_err) == 1
    assert ev_err[0].kind == CliEventKind.RESULT
    assert ev_err[0].text == "fatal authentication failure"
    assert ev_err[0].result is not None
    assert ev_err[0].result.is_error is True
    assert ev_err[0].result.result_text == "fatal authentication failure"
    assert tr_err.result is not None
    assert tr_err.result.is_error is True
    assert tr_err.result.result_text == "fatal authentication failure"

    tr_failed = CodexJsonlTranslator()
    ev_failed = tr_failed.feed_line(
        '{"type": "turn.failed", "error": {"message": "rate limit exceeded"}}'
    )
    assert len(ev_failed) == 1
    assert ev_failed[0].kind == CliEventKind.RESULT
    assert ev_failed[0].text == "rate limit exceeded"
    assert ev_failed[0].result is not None
    assert ev_failed[0].result.is_error is True
    assert tr_failed.result is not None
    assert tr_failed.result.is_error is True
    assert tr_failed.result.result_text == "rate limit exceeded"


def test_item_updated_and_completed_suffix_deduplication() -> None:
    tr = CodexJsonlTranslator()
    tr.feed_line('{"type":"thread.started","thread_id":"th_dedup"}')

    # First partial update
    ev1 = tr.feed_line(
        '{"type":"item.updated","item":{"id":"item_0","type":"agent_message","text":"Hello"}}'
    )
    assert [e.kind for e in ev1] == [CliEventKind.TEXT_DELTA]
    assert ev1[0].text == "Hello"

    # Duplicate update with identical text -> no new TEXT_DELTA
    ev2 = tr.feed_line(
        '{"type":"item.updated","item":{"id":"item_0","type":"agent_message","text":"Hello"}}'
    )
    assert ev2 == []

    # Second update with longer text -> only new suffix
    ev3 = tr.feed_line(
        '{"type":"item.updated","item":{"id":"item_0","type":"agent_message","text":"Hello, world"}}'
    )
    assert [e.kind for e in ev3] == [CliEventKind.TEXT_DELTA]
    assert ev3[0].text == ", world"

    # Completed with final suffix -> emits only "!" plus ASSISTANT_MESSAGE
    ev4 = tr.feed_line(
        '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"Hello, world!"}}'
    )
    assert [e.kind for e in ev4] == [
        CliEventKind.TEXT_DELTA,
        CliEventKind.ASSISTANT_MESSAGE,
    ]
    assert ev4[0].text == "!"
    assert ev4[1].message_id == "item_0"

    # Turn completed accumulates the exact text
    ev5 = tr.feed_line(
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":5}}'
    )
    assert tr.result is not None
    assert tr.result.result_text == "Hello, world!"
    assert any(e.kind == CliEventKind.RESULT for e in ev5)


def test_forbidden_shell_fixture_preserves_raw_item_type() -> None:
    tr = CodexJsonlTranslator()
    events = _run_lines(tr, _load_fixture_lines("forbidden_shell.jsonl"))

    forbidden_events = [
        ev
        for ev in events
        if isinstance(ev.raw.get("item"), dict)
        and ev.raw["item"].get("type") == "command_execution"
    ]
    assert len(forbidden_events) >= 1
    for ev in forbidden_events:
        assert ev.kind == CliEventKind.TOOL_USE_START
        assert ev.tool_name == "command_execution"
        assert ev.raw["item"]["type"] == "command_execution"
        assert (
            find_forbidden_item_kind(
                frozenset({"command_execution", "file_change", "web_search"}),
                raw=ev.raw,
                tool_name=ev.tool_name,
            )
            == "command_execution"
        )


def test_auth_error_and_bad_model_fixtures_end_with_final_error() -> None:
    tr_auth = CodexJsonlTranslator()
    events_auth = _run_lines(tr_auth, _load_fixture_lines("auth_error.jsonl"))
    assert tr_auth.result is not None
    assert tr_auth.result.is_error is True
    assert "401 Unauthorized" in tr_auth.result.result_text
    assert not tr_auth.result.result_text.startswith("Reconnecting...")
    assert not tr_auth.result.result_text.startswith("Falling back")
    result_events_auth = [
        ev for ev in events_auth if ev.kind == CliEventKind.RESULT
    ]
    assert len(result_events_auth) == 1

    tr_bad = CodexJsonlTranslator()
    events_bad = _run_lines(tr_bad, _load_fixture_lines("bad_model.jsonl"))
    assert tr_bad.result is not None
    assert tr_bad.result.is_error is True
    assert (
        tr_bad.result.result_text
        == "The 'gpt-nonexistent-model-9999' model is not supported when using Codex with a ChatGPT account."
    )
    result_events_bad = [
        ev for ev in events_bad if ev.kind == CliEventKind.RESULT
    ]
    assert len(result_events_bad) == 1


def test_reasoning_events_top_level_and_item_with_summary_blocks() -> None:
    tr = CodexJsonlTranslator()
    tr.feed_line('{"type":"thread.started","thread_id":"th_reason"}')

    # Top-level reasoning with text
    ev1 = tr.feed_line(
        '{"type":"reasoning","id":"r_1","text":"Step 1"}'
    )
    assert len(ev1) == 1
    assert ev1[0].kind == CliEventKind.THINKING_DELTA
    assert ev1[0].text == "Step 1"

    # Duplicate top-level reasoning -> ignored
    assert (
        tr.feed_line('{"type":"reasoning","id":"r_1","text":"Step 1"}') == []
    )

    # Extended top-level reasoning -> only suffix emitted
    ev2 = tr.feed_line(
        '{"type":"reasoning","id":"r_1","text":"Step 1: analyze"}'
    )
    assert len(ev2) == 1
    assert ev2[0].kind == CliEventKind.THINKING_DELTA
    assert ev2[0].text == ": analyze"

    # item.updated / item.completed with item.type == "reasoning" and summary blocks
    ev3 = tr.feed_line(
        json.dumps(
            {
                "type": "item.updated",
                "item": {
                    "id": "r_2",
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "Checking "}],
                },
            }
        )
    )
    assert len(ev3) == 1
    assert ev3[0].kind == CliEventKind.THINKING_DELTA
    assert ev3[0].text == "Checking "

    ev4 = tr.feed_line(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "r_2",
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": "Checking "},
                        {"type": "summary_text", "text": "constraints."},
                    ],
                },
            }
        )
    )
    assert len(ev4) == 1
    assert ev4[0].kind == CliEventKind.THINKING_DELTA
    assert ev4[0].text == "constraints."


def test_mcp_tool_call_completed_without_started_and_duplicate_protection() -> None:
    tr = CodexJsonlTranslator()
    tr.feed_line('{"type":"thread.started","thread_id":"th_tool"}')

    # Completed arriving without a preceding item.started
    events = tr.feed_line(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "call_no_start",
                    "type": "mcp_tool_call",
                    "tool": "read_file",
                    "arguments": '{"path": "/tmp/a.txt"}',
                    "result": None,
                    "error": {"message": "Permission denied"},
                },
            }
        )
    )
    assert [e.kind for e in events] == [
        CliEventKind.TOOL_USE_START,
        CliEventKind.TOOL_USE_END,
        CliEventKind.TOOL_RESULT,
    ]
    assert events[0].tool_use_id == "call_no_start"
    assert events[0].tool_name == "read_file"
    assert events[0].tool_input == {"path": "/tmp/a.txt"}
    assert events[1].tool_input == {"path": "/tmp/a.txt"}
    assert events[2].text == "Permission denied"

    # Duplicate item.completed for the same tool call ID should not re-emit
    dup_events = tr.feed_line(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "call_no_start",
                    "type": "mcp_tool_call",
                    "tool": "read_file",
                    "arguments": {"path": "/tmp/a.txt"},
                    "result": None,
                    "error": {"message": "Permission denied"},
                },
            }
        )
    )
    assert dup_events == []


def test_reconnect_error_flushed_only_when_stream_ends_abruptly() -> None:
    # 1. Stream ends right after Reconnecting... without turn.failed or turn.completed
    tr_abrupt = CodexJsonlTranslator()
    tr_abrupt.feed_line('{"type":"thread.started","thread_id":"th_abrupt"}')
    assert (
        tr_abrupt.feed_line(
            '{"type":"error","message":"Reconnecting... 1/5 (network drop)"}'
        )
        == []
    )
    assert tr_abrupt.result is None
    flushed = tr_abrupt.flush_pending()
    assert len(flushed) == 1
    assert flushed[0].kind == CliEventKind.RESULT
    assert tr_abrupt.result is not None
    assert tr_abrupt.result.is_error is True
    assert "Reconnecting... 1/5" in tr_abrupt.result.result_text
    # Second flush is a no-op
    assert tr_abrupt.flush_pending() == []

    # 2. Stream recovers after Reconnecting... and completes normally
    tr_recovered = CodexJsonlTranslator()
    tr_recovered.feed_line('{"type":"thread.started","thread_id":"th_rec"}')
    assert (
        tr_recovered.feed_line(
            '{"type":"error","message":"Reconnecting... 1/5 (transient)"}'
        )
        == []
    )
    tr_recovered.feed_line(
        '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"Recovered OK"}}'
    )
    tr_recovered.feed_line(
        '{"type":"turn.completed","usage":{"input_tokens":50,"cached_input_tokens":10,"output_tokens":5}}'
    )
    assert tr_recovered.flush_pending() == []
    assert tr_recovered.result is not None
    assert tr_recovered.result.is_error is False
    assert tr_recovered.result.result_text == "Recovered OK"


def test_multi_turn_reuse_of_item_ids_after_reset_or_turn_started() -> None:
    tr = CodexJsonlTranslator()
    tr.feed_line('{"type":"thread.started","thread_id":"th_multi"}')
    tr.feed_line('{"type":"turn.started"}')
    ev_t1 = tr.feed_line(
        '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"Turn 1"}}'
    )
    tr.feed_line(
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":2}}'
    )
    assert [e.text for e in ev_t1 if e.kind == CliEventKind.TEXT_DELTA] == [
        "Turn 1"
    ]
    assert tr.result is not None
    assert tr.result.result_text == "Turn 1"

    # Reset for turn 2 where item_0 is reused with identical or new text
    tr.reset_result()
    assert tr.result is None
    tr.feed_line('{"type":"turn.started"}')
    ev_t2 = tr.feed_line(
        '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"Turn 1"}}'
    )
    assert [e.text for e in ev_t2 if e.kind == CliEventKind.TEXT_DELTA] == [
        "Turn 1"
    ]


@pytest.mark.parametrize(
    "forbidden_type",
    ["command_execution", "file_change", "web_search"],
)
def test_all_forbidden_item_kinds_preserve_raw_type(
    forbidden_type: str,
) -> None:
    tr = CodexJsonlTranslator()
    events = tr.feed_line(
        json.dumps(
            {
                "type": "item.started",
                "item": {"id": "item_x", "type": forbidden_type},
            }
        )
    )
    assert len(events) == 1
    assert events[0].kind == CliEventKind.TOOL_USE_START
    assert events[0].tool_name == forbidden_type
    assert events[0].raw["item"]["type"] == forbidden_type


def test_adversarial_usage_and_non_string_fields() -> None:
    tr = CodexJsonlTranslator()
    # Non-string thread_id or model types should not crash or stringify dicts
    ev_init = tr.feed_line(
        json.dumps(
            {
                "type": "thread.started",
                "thread_id": {"unexpected": "dict"},
                "model": ["unexpected", "list"],
            }
        )
    )
    assert len(ev_init) == 1
    assert tr.init is not None
    assert tr.init.session_id == ""
    assert tr.init.model == "codex"

    # cached_input_tokens > input_tokens and boolean/negative values in usage
    ev_turn = tr.feed_line(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 250,
                    "cache_write_input_tokens": -5,
                    "output_tokens": True,
                    "reasoning_output_tokens": False,
                },
            }
        )
    )
    usage = [e for e in ev_turn if e.kind == CliEventKind.USAGE][0].usage
    assert usage is not None
    assert usage.input_tokens == 0
    assert usage.cache_read_input_tokens == 250
    assert usage.cache_creation_input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.reasoning_tokens is None


def test_resume_fixture_and_error_followed_by_turn_failed_variants() -> None:
    tr_resume = CodexJsonlTranslator(model="gpt-5.4")
    events_resume = _run_lines(tr_resume, _load_fixture_lines("resume.jsonl"))
    text = "".join(
        e.text for e in events_resume if e.kind == CliEventKind.TEXT_DELTA
    )
    assert text == "PONG_CODEX"
    assert tr_resume.result is not None
    assert tr_resume.result.is_error is False
    assert tr_resume.result.usage.input_tokens == 16154 - 12800
    assert tr_resume.result.usage.cache_read_input_tokens == 12800

    # When error is followed by turn.failed with a DIFFERENT message, result is updated
    tr_diff = CodexJsonlTranslator()
    ev1 = tr_diff.feed_line('{"type":"error","message":"initial error"}')
    assert len(ev1) == 1
    assert tr_diff.result is not None
    assert tr_diff.result.result_text == "initial error"

    ev2 = tr_diff.feed_line(
        '{"type":"turn.failed","error":{"message":"refined final error"}}'
    )
    assert len(ev2) == 1
    assert tr_diff.result.result_text == "refined final error"


@pytest.mark.parametrize(
    "packet,expected_forbidden",
    [
        (
            {
                "type": "item.updated",
                "item": {"id": "item_u1", "type": "command_execution"},
            },
            "command_execution",
        ),
        (
            {
                "type": "item.completed",
                "item": {"id": "item_c1", "type": "file_change"},
            },
            "file_change",
        ),
        (
            {
                "type": "item.delta",
                "item": {"id": "item_d1", "type": "web_search"},
            },
            "web_search",
        ),
        (
            {
                "type": "event_msg",
                "payload": {"item": {"id": "item_p1", "type": "command_execution"}},
            },
            "command_execution",
        ),
        (
            {
                "type": "turn.updated",
                "turn": {"items": [{"id": "item_t1", "kind": "file_change"}]},
            },
            "file_change",
        ),
        (
            {
                "type": "item.updated",
                "item": {
                    "id": "item_m1",
                    "type": "mcp_tool_call",
                    "tool": "web_search",
                },
            },
            "web_search",
        ),
    ],
)
def test_forbidden_item_kinds_detected_across_lifecycle_and_nested_shapes(
    packet: dict[str, object], expected_forbidden: str
) -> None:
    tr = CodexJsonlTranslator()
    events = tr.feed_line(json.dumps(packet))
    assert len(events) == 1
    assert events[0].kind == CliEventKind.TOOL_USE_START
    assert events[0].tool_name == expected_forbidden
    assert (
        find_forbidden_item_kind(
            frozenset({"command_execution", "file_change", "web_search"}),
            raw=events[0].raw,
            tool_name=events[0].tool_name,
        )
        == expected_forbidden
    )



