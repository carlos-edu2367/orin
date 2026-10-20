"""W6 Task 14: an SDK host's hooks (type "callback") come only from the cli layer and are answered by the host."""
from __future__ import annotations

import json

from agentos.agentic.lifecycle import HookEvent
from agentos.hooks.config import parse_hook_mapping
from agentos.hooks.protocol import decision_from_command
from agentos.hooks.runners import CallbackHookRunner

DECLARED = {"PreToolUse": [{"matcher": "Write", "hooks": [{"type": "callback", "callback_id": "hook_0", "timeout": 5}]}]}


def test_callback_hooks_come_only_from_the_cli_layer() -> None:
    specs, errors = parse_hook_mapping(DECLARED, "cli")
    assert errors == () and [(spec.type, spec.callback_id, spec.timeout, spec.matcher) for spec in specs] == [("callback", "hook_0", 5.0, "Write")]
    for source in ("user", "project", "local", "managed", "plugin:x"):
        specs, errors = parse_hook_mapping(DECLARED, source)
        assert specs == () and errors and "SDK host" in errors[0].message


def test_a_callback_needs_an_id() -> None:
    _, errors = parse_hook_mapping({"PreToolUse": [{"hooks": [{"type": "callback", "callback_id": "../x y"}]}]}, "cli")
    assert errors and "callback_id" in errors[0].message


def test_two_callbacks_are_two_hooks() -> None:
    specs, _ = parse_hook_mapping({"PreToolUse": [{"hooks": [{"type": "callback", "callback_id": "hook_0"},
                                                            {"type": "callback", "callback_id": "hook_1"}]}]}, "cli")
    assert len({spec.identity() for spec in specs}) == 2


def test_the_runner_reads_the_hosts_answer_as_a_command_hooks_output() -> None:
    asked: list[tuple] = []
    answer = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "no"}}
    runner = CallbackHookRunner(lambda kind, request, timeout: asked.append((kind, request, timeout)) or answer)
    (spec,), _ = parse_hook_mapping(DECLARED, "cli")
    run = runner.run(spec, {"tool_name": "Write", "tool_use_id": "tool-call:1"})
    assert asked == [("hook_callback", {"callback_id": "hook_0", "input": {"tool_name": "Write", "tool_use_id": "tool-call:1"},
                                        "tool_use_id": "tool-call:1"}, 5.0)]
    assert run.exit_code == 0 and json.loads(run.stdout) == answer
    assert decision_from_command(HookEvent.PRE_TOOL_USE, run).permission_decision == "deny"


def test_no_answer_and_an_error_are_failures() -> None:
    (spec,), _ = parse_hook_mapping(DECLARED, "cli")
    assert CallbackHookRunner(lambda *_: None).run(spec, {}).timed_out is True
    failed = CallbackHookRunner(lambda *_: {"_error": "host crashed"}).run(spec, {})
    assert failed.exit_code is None and failed.error == "host crashed"
