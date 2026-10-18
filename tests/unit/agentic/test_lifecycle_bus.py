from __future__ import annotations

import threading

from agentos.agentic.lifecycle import (
    CLAUDE_HOOK_EVENTS,
    NON_BLOCKING_EVENTS,
    HookEvent,
    LifecycleBus,
    LifecycleDecision,
)


def test_catalog_matches_claude_code() -> None:
    assert [event.value for event in CLAUDE_HOOK_EVENTS] == [
        "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch", "Notification",
        "UserPromptSubmit", "UserPromptExpansion", "SessionStart", "SessionEnd", "Stop", "StopFailure",
        "SubagentStart", "SubagentStop", "PreCompact", "PostCompact", "PreModelSwitch", "PostModelSwitch",
        "PermissionRequest", "PermissionDenied", "Setup", "TeammateIdle", "TaskCreated", "TaskCompleted",
        "Elicitation", "ElicitationResult", "ConfigChange", "WorktreeCreate", "WorktreeRemove",
        "InstructionsLoaded", "CwdChanged", "FileChanged", "DirectoryAdded", "MessageDisplay",
    ]
    assert len(set(HookEvent)) == 35
    assert HookEvent.STATUS_LINE.value == "StatusLine" and HookEvent.FILE_SUGGESTION.value == "FileSuggestion"
    assert {event.value for event in NON_BLOCKING_EVENTS} == {
        "Notification", "SessionStart", "SessionEnd", "Setup", "StopFailure", "SubagentStart",
        "PostToolUseFailure", "PostCompact", "PostModelSwitch", "PermissionDenied", "WorktreeCreate",
        "WorktreeRemove", "InstructionsLoaded", "CwdChanged", "FileChanged", "DirectoryAdded",
        "MessageDisplay", "StatusLine", "FileSuggestion",
    }


def test_emit_merges_base_payload_and_event_name() -> None:
    bus = LifecycleBus(base={"session_id": "s1"})
    bus.bind(cwd="/w", permission_mode="default", ignored=None)
    seen = []
    bus.subscribe(seen.append)
    decision = bus.emit(HookEvent.PRE_TOOL_USE, tool_name="Read", tool_input={"file_path": "a"})
    assert isinstance(decision, LifecycleDecision) and decision.permission_decision is None
    payload = dict(seen[0].payload)
    assert seen[0].name is HookEvent.PRE_TOOL_USE
    assert payload == {
        "session_id": "s1", "cwd": "/w", "permission_mode": "default", "transcript_path": "",
        "tool_name": "Read", "tool_input": {"file_path": "a"}, "hook_event_name": "PreToolUse",
    }
    assert bus.emit("PreToolUse").permission_decision is None  # string names accepted


def test_decisions_merge_deny_over_ask_over_allow() -> None:
    bus = LifecycleBus()
    bus.subscribe(lambda event: LifecycleDecision(permission_decision="allow", updated_input={"a": 1}, additional_context=["one"]))
    bus.subscribe(lambda event: LifecycleDecision(permission_decision="deny", reason="no", additional_context=["two"]))
    bus.subscribe(lambda event: LifecycleDecision(permission_decision="ask", updated_input={"a": 2}))
    decision = bus.emit(HookEvent.PRE_TOOL_USE, tool_name="Bash")
    assert decision.permission_decision == "deny"
    assert decision.reason == "no"
    assert decision.updated_input == {"a": 1}
    assert decision.additional_context == ["one", "two"]


def test_blocked_and_stop_are_sticky() -> None:
    bus = LifecycleBus()
    bus.subscribe(lambda event: LifecycleDecision(blocked=True, reason="keep going"))
    bus.subscribe(lambda event: LifecycleDecision(stop=True, stop_reason="halt", system_messages=["note"]))
    decision = bus.emit(HookEvent.STOP)
    assert decision.blocked and decision.stop and decision.stop_reason == "halt"
    assert decision.system_messages == ["note"]


def test_non_blocking_events_discard_control_but_keep_session_start_context() -> None:
    bus = LifecycleBus()
    bus.subscribe(lambda event: LifecycleDecision(blocked=True, permission_decision="deny", additional_context=["ctx"], system_messages=["m"]))
    post = bus.emit(HookEvent.POST_COMPACT)
    assert not post.blocked and post.permission_decision is None and post.additional_context == []
    assert post.system_messages == ["m"]
    start = bus.emit(HookEvent.SESSION_START)
    assert not start.blocked and start.additional_context == ["ctx"]


def test_a_failing_subscriber_never_breaks_the_emitter() -> None:
    bus = LifecycleBus()
    seen = []

    def broken(event):
        raise RuntimeError("boom")

    bus.subscribe(broken)
    bus.subscribe(seen.append)
    bus.subscribe(lambda event: "not a decision")
    decision = bus.emit(HookEvent.POST_TOOL_USE)
    assert len(seen) == 1 and decision.permission_decision is None


def test_unsubscribe_and_thread_safety() -> None:
    bus = LifecycleBus()
    counter = {"n": 0}
    lock = threading.Lock()

    def count(event):
        with lock:
            counter["n"] += 1

    unsubscribe = bus.subscribe(count)
    threads = [threading.Thread(target=lambda: [bus.emit(HookEvent.POST_TOOL_USE) for _ in range(50)]) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert counter["n"] == 200
    unsubscribe()
    unsubscribe()  # idempotent
    bus.emit(HookEvent.POST_TOOL_USE)
    assert counter["n"] == 200


def test_payload_is_read_only() -> None:
    bus = LifecycleBus()
    seen = []
    bus.subscribe(seen.append)
    bus.emit(HookEvent.STOP, value=1)
    try:
        seen[0].payload["value"] = 2  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("payload must be immutable")


def test_updated_tool_output_merges_first_and_is_dropped_for_non_blocking() -> None:
    from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision

    bus = LifecycleBus()
    bus.subscribe(lambda event: LifecycleDecision(updated_tool_output={"a": 1}))
    bus.subscribe(lambda event: LifecycleDecision(updated_tool_output={"a": 2}))
    assert bus.emit(HookEvent.POST_TOOL_USE).updated_tool_output == {"a": 1}
    assert bus.emit(HookEvent.POST_COMPACT).updated_tool_output is None
