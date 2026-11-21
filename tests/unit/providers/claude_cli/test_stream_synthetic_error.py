from __future__ import annotations

import json
from pathlib import Path

from agentos.providers.claude_cli.stream import StreamJsonParser
from agentos.providers.cli_adapter import CliEventKind

FIXTURES = Path(__file__).parent / "fixtures"


def _parse_fixture(name: str) -> tuple[StreamJsonParser, list]:
    parser = StreamJsonParser()
    events = []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        events.extend(parser.feed_line(line))
    return parser, events


def test_synthetic_marker_discards_text_delta_and_records_synthetic_error_text() -> None:
    parser, events = _parse_fixture("prompt-too-long.jsonl")
    text_deltas = [ev for ev in events if ev.kind == CliEventKind.TEXT_DELTA]
    assert text_deltas == []
    assert parser.synthetic_error_text == "Prompt is too long"
    assert parser.result is not None
    assert parser.result.is_error is True
    assert parser.result.result_text == "Prompt is too long"
    # Zero-usage synthetic API error message must not emit a misleading zero USAGE event.
    zero_usages = [
        ev
        for ev in events
        if ev.kind == CliEventKind.USAGE
        and ev.usage is not None
        and ev.usage.context_tokens == 0
        and ev.usage.output_tokens == 0
    ]
    assert zero_usages == []


def test_no_marker_fixture_emits_text_delta_for_runner_fallback() -> None:
    parser, events = _parse_fixture("prompt-too-long-no-marker.jsonl")
    text_deltas = [ev for ev in events if ev.kind == CliEventKind.TEXT_DELTA]
    assert len(text_deltas) == 1
    assert text_deltas[0].text == "Prompt is too long"
    assert parser.synthetic_error_text is None
    assert parser.result is not None
    assert parser.result.is_error is True


def test_is_api_error_message_without_synthetic_model_discards_text_delta() -> None:
    parser = StreamJsonParser()
    packet = {
        "type": "assistant",
        "isApiErrorMessage": True,
        "message": {
            "id": "msg_api_err",
            "model": "claude-fable-5-2",
            "role": "assistant",
            "content": [{"type": "text", "text": "Prompt is too long"}],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }
    events = parser.feed_line(json.dumps(packet))
    assert [ev for ev in events if ev.kind == CliEventKind.TEXT_DELTA] == []
    assert parser.synthetic_error_text == "Prompt is too long"


def test_stream_event_synthetic_message_discards_text_deltas() -> None:
    parser = StreamJsonParser()
    lines = [
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "message_start",
                    "message": {
                        "id": "msg_syn_stream",
                        "model": "<synthetic>",
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Prompt is too long"},
                },
            }
        ),
    ]
    events = []
    for line in lines:
        events.extend(parser.feed_line(line))
    assert [ev for ev in events if ev.kind == CliEventKind.TEXT_DELTA] == []
    assert parser.synthetic_error_text == "Prompt is too long"
