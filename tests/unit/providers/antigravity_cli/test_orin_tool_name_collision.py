"""Orin's chat-mode tools share names with agy built-ins; only the real built-in may stop the process.

Chat mode exposes Orin's tools as `run_command`, `read_file`, `view_file`. agy reports them prefixed
(`mcp_orin_run_command`) and the translator hands the bare name on. Checking that bare name against
the built-in list killed agy after every Orin tool call ("Conexão com o modelo caiu; retomando"),
and a paused turn was resumed again and again.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.providers.antigravity_cli.adapter import AntigravityCliAdapter
from agentos.providers.antigravity_cli.stream import (
    FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
    AntigravityStreamTranslator,
)
from agentos.providers.claude_cli.native_loop import find_forbidden_item_kinds

FIXTURE = Path(__file__).parent / "fixtures" / "tool_call.jsonl"


def _forbidden_hits(tool: str) -> list[tuple[str, str | None, list[str]]]:
    """Feed the real agy tool-call transcript, with the Orin tool renamed, through the same checks the native loop runs."""
    caps = AntigravityCliAdapter().capabilities
    translator = AntigravityStreamTranslator()
    hits: list[tuple[str, str | None, list[str]]] = []
    for raw in FIXTURE.read_text(encoding="utf-8").splitlines():
        line = raw.replace("mcp_orin_echo", tool)
        line_hits = find_forbidden_item_kinds(caps.forbidden_item_kinds, raw_line=line)
        if line_hits:
            hits.append(("raw_line", None, line_hits))
        for ev in translator.feed_line(line):
            ev_hits = find_forbidden_item_kinds(
                caps.forbidden_item_kinds,
                raw=ev.raw if isinstance(ev.raw, dict) else None,
                tool_name=ev.tool_name,
            )
            if ev_hits:
                hits.append((ev.kind.value, ev.tool_name, ev_hits))
    return hits


@pytest.mark.parametrize("orin_tool", ["run_command", "read_file", "view_file"])
def test_orin_tool_named_like_a_builtin_is_not_forbidden(orin_tool: str) -> None:
    assert orin_tool in FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS  # the collision this guards against
    assert _forbidden_hits(f"mcp_orin_{orin_tool}") == []


@pytest.mark.parametrize("builtin", ["run_command", "write_to_file"])
def test_real_builtin_with_the_same_name_is_still_forbidden(builtin: str) -> None:
    hits = _forbidden_hits(builtin)
    assert hits, "an unprefixed agy built-in must still stop the process"
    assert any(builtin in found for _, _, found in hits)


def test_bare_tool_name_without_raw_event_is_still_checked() -> None:
    caps = AntigravityCliAdapter().capabilities
    assert find_forbidden_item_kinds(caps.forbidden_item_kinds, tool_name="run_command") == ["run_command"]


def test_orin_prefix_on_another_tool_does_not_clear_a_builtin() -> None:
    """A step naming `mcp_orin_read_file` must not excuse a bare `run_command` in the same check."""
    caps = AntigravityCliAdapter().capabilities
    raw = {
        "event": "step_update",
        "step_update": {
            "step_type": "tool",
            "state": "DONE",
            "tool_name": "mcp_orin_read_file",
            "tool_info": {"name": "mcp_orin_read_file", "parameters": {}},
        },
    }
    assert find_forbidden_item_kinds(caps.forbidden_item_kinds, raw=raw, tool_name="run_command") == [
        "run_command"
    ]
    assert find_forbidden_item_kinds(caps.forbidden_item_kinds, raw=raw, tool_name="read_file") == []
    assert json.dumps(raw)  # payload stays plain JSON, like agy's
