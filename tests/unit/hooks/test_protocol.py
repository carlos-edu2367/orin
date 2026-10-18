from __future__ import annotations

from agentos.agentic.lifecycle import HookEvent as E
from agentos.hooks.protocol import HookRun, decision_from_command, decision_from_verdict, parse_verdict


def test_exit_two_blocks_and_denies_tools() -> None:
    decision = decision_from_command(E.PRE_TOOL_USE, HookRun(2, "", "no rm please\n"))
    assert decision.blocked and decision.permission_decision == "deny" and decision.reason == "no rm please"
    stop = decision_from_command(E.STOP, HookRun(2, "", ""))
    assert stop.blocked and stop.permission_decision is None and stop.reason == "blocked by hook"


def test_other_failures_are_non_blocking_unless_on_failure_block() -> None:
    assert not decision_from_command(E.PRE_TOOL_USE, HookRun(1, "", "oops")).blocked
    assert not decision_from_command(E.PRE_TOOL_USE, HookRun(None, "", "", timed_out=True)).blocked
    blocked = decision_from_command(E.PRE_TOOL_USE, HookRun(None, "", "", timed_out=True), on_failure="block")
    assert blocked.blocked and blocked.permission_decision == "deny" and "timed out" in blocked.reason


def test_plain_stdout_is_context_only_for_context_events() -> None:
    assert decision_from_command(E.SESSION_START, HookRun(0, "branch: main\n", "")).additional_context == ["branch: main"]
    assert decision_from_command(E.USER_PROMPT_SUBMIT, HookRun(0, "ctx", "")).additional_context == ["ctx"]
    assert decision_from_command(E.POST_TOOL_USE, HookRun(0, "ignored", "")).additional_context == []


def test_json_control() -> None:
    pre = decision_from_command(E.PRE_TOOL_USE, HookRun(0, '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "check", "updatedInput": {"command": "ls -la"}, "additionalContext": "note"}, "systemMessage": "hi"}', ""))
    assert pre.permission_decision == "ask" and pre.reason == "check" and pre.updated_input == {"command": "ls -la"}
    assert pre.additional_context == ["note"] and pre.system_messages == ["hi"]
    stop = decision_from_command(E.STOP, HookRun(0, '{"decision": "block", "reason": "tests not run"}', ""))
    assert stop.blocked and stop.reason == "tests not run"
    halt = decision_from_command(E.POST_TOOL_USE, HookRun(0, '{"continue": false, "stopReason": "enough"}', ""))
    assert halt.stop and halt.stop_reason == "enough"
    legacy = decision_from_command(E.PRE_TOOL_USE, HookRun(0, '{"decision": "approve"}', ""))
    assert legacy.permission_decision == "allow"
    request = decision_from_command(E.PERMISSION_REQUEST, HookRun(0, '{"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": "nope", "interrupt": true}}}', ""))
    assert request.permission_decision == "deny" and request.reason == "nope" and request.stop
    mcp = decision_from_command(E.POST_TOOL_USE, HookRun(0, '{"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedMCPToolOutput": {"x": 1}}}', ""))
    assert mcp.updated_tool_output == {"x": 1}
    junk = decision_from_command(E.PRE_TOOL_USE, HookRun(0, '{"hookSpecificOutput": {"permissionDecision": "maybe"}}', ""))
    assert junk.permission_decision is None
    assert decision_from_command(E.PRE_TOOL_USE, HookRun(0, "{not json}", "")).permission_decision is None


def test_verdicts() -> None:
    assert parse_verdict('thinking... {"ok": false, "reason": "no"}') == {"ok": False, "reason": "no"}
    assert parse_verdict('{"ok": true} then {"ok": false}') is None
    assert parse_verdict("nothing") is None
    denied = decision_from_verdict(E.PRE_TOOL_USE, {"ok": False, "reason": "unsafe"})
    assert denied.blocked and denied.permission_decision == "deny" and denied.reason == "unsafe"
    assert not decision_from_verdict(E.STOP, {"ok": True}).blocked
    assert not decision_from_verdict(E.STOP, None).blocked
    assert decision_from_verdict(E.STOP, None, on_failure="block").blocked
