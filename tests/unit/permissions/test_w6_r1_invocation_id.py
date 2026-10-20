"""W6-R1: a permission request records the tool invocation it is for (same thread), and no other."""
from __future__ import annotations

import threading
from pathlib import Path

from agentos.agentic.call_context import ActiveInvocation, current_invocation_id
from agentos.code_mode.permissions import ToolPermissionBroker


class _Toolset(ActiveInvocation):
    def __init__(self) -> None:
        self._tls = threading.local()


def test_the_request_record_and_event_carry_the_invocation_set_in_this_thread(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    broker = ToolPermissionBroker(root)
    events: list[tuple[str, str, dict]] = []
    broker.event_emitter = lambda kind, summary, payload: events.append((kind, summary, payload))
    toolset = _Toolset()
    toolset._active_invocation_id = "call_A"

    record = broker.create_permission_request(tool_name="run_command", arguments={"command": "rm -rf x"})

    assert record["invocation_id"] == "call_A"
    (kind, _summary, payload), = events
    assert kind == "code_mode.decision_required"
    assert payload["invocation_id"] == "call_A"
    assert payload["permission_request"]["invocation_id"] == "call_A"


def test_another_thread_sees_no_invocation(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    broker = ToolPermissionBroker(root)
    toolset = _Toolset()
    toolset._active_invocation_id = "call_A"
    seen: list[object] = []

    def other_thread() -> None:
        seen.append(current_invocation_id())
        seen.append(broker.create_permission_request(tool_name="run_command", arguments={"command": "ls"})["invocation_id"])

    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join()
    assert seen == [None, None]


def test_setting_none_clears_the_invocation_and_the_previous_value_is_restored() -> None:
    toolset = _Toolset()
    toolset._active_invocation_id = "call_A"
    previous = toolset._active_invocation_id
    toolset._active_invocation_id = "call_B"
    assert current_invocation_id() == "call_B"
    toolset._active_invocation_id = previous
    assert current_invocation_id() == "call_A"
    toolset._active_invocation_id = None
    assert current_invocation_id() is None
