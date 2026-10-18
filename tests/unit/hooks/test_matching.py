from __future__ import annotations

import pytest

from agentos.agentic.lifecycle import HookEvent as E
from agentos.hooks.matching import matcher_applies


@pytest.mark.parametrize(
    ("matcher", "tool", "expected"),
    [
        ("", "Bash", True), ("*", "Bash", True), ("Bash", "Bash", True), ("Bash", "bash", False),
        ("Bash", "BashX", False), ("Edit|Write", "Write", True), ("Edit|Write", "Read", False),
        ("Notebook.*", "NotebookEdit", True), ("mcp__github__.*", "mcp__github__list", True),
        ("mcp__github__.*", "mcp__gitlab__list", False), ("(", "Bash", False),
    ],
)
def test_tool_matchers(matcher: str, tool: str, expected: bool) -> None:
    for event in (E.PRE_TOOL_USE, E.POST_TOOL_USE, E.POST_TOOL_USE_FAILURE, E.PERMISSION_REQUEST, E.PERMISSION_DENIED):
        assert matcher_applies(event, matcher, {"tool_name": tool}) is expected


def test_other_fields_and_events_without_matchers() -> None:
    assert matcher_applies(E.SESSION_START, "startup", {"source": "startup"})
    assert not matcher_applies(E.SESSION_START, "resume", {"source": "startup"})
    assert matcher_applies(E.PRE_COMPACT, "auto", {"trigger": "auto"})
    assert matcher_applies(E.SUBAGENT_STOP, "Explore", {"agent_type": "Explore"})
    assert matcher_applies(E.STOP, "anything", {})          # Stop has no matcher field
    assert matcher_applies(E.USER_PROMPT_SUBMIT, "x", {"prompt": "y"})


def test_long_targets_do_not_run_regexes() -> None:
    assert not matcher_applies(E.PRE_TOOL_USE, "a.*", {"tool_name": "a" * 600})
    assert matcher_applies(E.PRE_TOOL_USE, "*", {"tool_name": "a" * 600})
