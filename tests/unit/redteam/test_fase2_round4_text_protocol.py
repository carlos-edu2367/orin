"""Red team round 4: text protocol history rendering."""
from __future__ import annotations

import re

from agentos.providers.claude_cli.text_protocol import TranscriptState


def _render(native_history: bool) -> str:
    msgs = [
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call#1 x", "type": "function", "function": {"name": "Read", "arguments": "{\"file_path\":\"a\"}"}}]},
        {"role": "tool", "tool_call_id": "call#1 x", "name": "Read", "content": "ok"},
        {"role": "user", "content": "e agora?"},
    ]
    blocks = TranscriptState().blocks_for(msgs, native_history=native_history)
    return "\n".join(str(b.get("text") or "") for b in blocks)


def test_tool_call_and_tool_result_ids_stay_paired_after_sanitizing() -> None:
    for native in (False, True):
        text = _render(native)
        call_ids = re.findall(r'(?:orin_tool_call id="|\[Tool Use id=)([^"\s\]]+)', text)
        result_ids = re.findall(r"\[Tool Result id=(.+?) name=", text)
        assert call_ids and result_ids, text
        assert call_ids[0] == result_ids[0], (native, call_ids, result_ids)
