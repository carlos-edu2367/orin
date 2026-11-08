from __future__ import annotations

import json

from agentos.providers.antigravity_cli.stream import AntigravityStreamTranslator
from agentos.providers.cli_adapter import CliEventKind


def test_antigravity_tool_events_read_then_write_carry_no_message_id() -> None:
    tr = AntigravityStreamTranslator()
    lines = [
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "conv-agy-1",
                    "step_index": 1,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "mcp_orin_read_file",
                    "tool_info": {
                        "name": "mcp_orin_read_file",
                        "parameters": {"path": "a.py"},
                        "output": "x = 1\n",
                    },
                },
            }
        ),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "conv-agy-1",
                    "step_index": 2,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "mcp_orin_write_file",
                    "tool_info": {
                        "name": "mcp_orin_write_file",
                        "parameters": {"path": "a.py", "content": "x = 2\n"},
                        "output": "ok",
                    },
                },
            }
        ),
    ]
    events = []
    for line in lines:
        events.extend(tr.feed_line(line))
    events.extend(tr.flush_pending())

    tool_events = [
        ev
        for ev in events
        if ev.kind
        in (
            CliEventKind.TOOL_USE_START,
            CliEventKind.TOOL_USE_END,
            CliEventKind.TOOL_RESULT,
        )
    ]
    assert len(tool_events) == 6
    assert all(ev.message_id is None for ev in tool_events)


def test_antigravity_step_update_with_thinking_emits_thinking_delta() -> None:
    from pathlib import Path
    from tests.unit.providers.cli_adapter_conformance import load_jsonl

    tr = AntigravityStreamTranslator()
    fixture_lines = load_jsonl(Path(__file__).parent / "fixtures" / "tool_call.jsonl")
    # Line 3 (index 2) is agent_response with thinking_tokens=31 and no text_delta
    events = tr.feed_line(fixture_lines[2])
    assert any(ev.kind == CliEventKind.THINKING_DELTA for ev in events)

