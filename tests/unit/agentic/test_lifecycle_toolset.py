from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.workspace import ConversationWorkspace


def _setup(tmp_path: Path, **kwargs):
    workspace = ConversationWorkspace(tmp_path, "conv-lifecycle")
    (workspace.root / "a.txt").write_text("hello\n", encoding="utf-8")
    (workspace.root / "sub").mkdir()
    bus = LifecycleBus(base={"session_id": "conv-lifecycle"})
    events = []
    bus.subscribe(events.append)
    return AgentToolset(workspace, lifecycle=bus, **kwargs), events


def _names(events):
    return [event.name for event in events]


def test_successful_call_emits_pre_and_post_with_public_shapes(tmp_path: Path) -> None:
    toolset, events = _setup(tmp_path)
    outcome = toolset.invoke("read_file", {"path": "a.txt"}, call_id="call-7")
    assert outcome.status == "succeeded"
    assert _names(events) == [HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE]
    pre, post = (dict(event.payload) for event in events)
    assert pre["tool_name"] == "Read" and pre["tool_input"] == {"file_path": "a.txt"} and pre["tool_use_id"] == "call-7"
    assert post["tool_name"] == "Read" and post["tool_response"]["status"] == "succeeded"
    assert pre["session_id"] == "conv-lifecycle"


def test_failed_call_emits_post_tool_use_failure(tmp_path: Path) -> None:
    toolset, events = _setup(tmp_path)
    outcome = toolset.invoke("read_file", {"path": "missing.txt"})
    assert outcome.status == "failed"
    assert _names(events) == [HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE_FAILURE]
    assert dict(events[1].payload)["error"]


def test_unknown_tool_still_emits_and_never_raises(tmp_path: Path) -> None:
    toolset, events = _setup(tmp_path)
    outcome = toolset.invoke("NoSuchTool", {})
    assert outcome.error_code == "UNKNOWN_TOOL"
    assert _names(events) == [HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE_FAILURE]


def test_plan_mode_denial_emits_permission_denied(tmp_path: Path) -> None:
    toolset, events = _setup(tmp_path, code_mode_active=True, permission_mode="plan_mode")
    outcome = toolset.invoke("write_file", {"path": "b.txt", "content": "x"})
    assert outcome.status == "failed"
    assert _names(events) == [HookEvent.PRE_TOOL_USE, HookEvent.PERMISSION_DENIED]
    assert dict(events[1].payload)["tool_name"] == "Write"


def test_task_events(tmp_path: Path) -> None:
    toolset, events = _setup(tmp_path)
    created = toolset.invoke("task_create", {"subject": "Write tests"})
    assert created.status == "succeeded"
    assert HookEvent.TASK_CREATED in _names(events)
    created_payload = dict(next(e for e in events if e.name is HookEvent.TASK_CREATED).payload)
    assert created_payload["task_subject"] == "Write tests"
    task_id = created_payload["task_id"]
    assert task_id
    events.clear()
    toolset.invoke("task_update", {"taskId": task_id, "status": "completed"})
    assert HookEvent.TASK_COMPLETED in _names(events)
    assert dict(next(e for e in events if e.name is HookEvent.TASK_COMPLETED).payload)["task_id"] == task_id


def test_cwd_change_emits_cwd_changed(tmp_path: Path) -> None:
    toolset, events = _setup(tmp_path)
    outcome = toolset.invoke("run_command", {"command": "cd sub"})
    assert outcome.status == "succeeded", outcome.content
    changed = [dict(e.payload) for e in events if e.name is HookEvent.CWD_CHANGED]
    assert len(changed) == 1
    assert changed[0]["new_cwd"].endswith("sub")


def test_no_bus_means_no_overhead_and_same_result(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-nobus")
    (workspace.root / "a.txt").write_text("hello\n", encoding="utf-8")
    assert AgentToolset(workspace).invoke("read_file", {"path": "a.txt"}).status == "succeeded"
