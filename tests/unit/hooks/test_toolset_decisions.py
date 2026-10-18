from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.lifecycle import HookEvent as E, LifecycleBus, LifecycleDecision
from agentos.agentic.workspace import ConversationWorkspace


def _toolset(tmp_path: Path, decide, **kwargs):
    workspace = ConversationWorkspace(tmp_path, "conv-hooks")
    (workspace.root / "a.txt").write_text("hello\n", encoding="utf-8")
    bus = LifecycleBus()
    seen = []

    def subscriber(event):
        seen.append(event)
        return decide(event)

    bus.subscribe(subscriber)
    return AgentToolset(workspace, lifecycle=bus, **kwargs), seen, workspace


def test_pre_tool_use_deny_blocks_the_call(tmp_path: Path) -> None:
    toolset, seen, _ = _toolset(tmp_path, lambda e: LifecycleDecision(permission_decision="deny", reason="not today") if e.name is E.PRE_TOOL_USE else None)
    outcome = toolset.invoke("read_file", {"path": "a.txt"})
    assert outcome.status == "failed" and outcome.error_code == "HOOK_DENIED" and "not today" in outcome.content
    assert E.POST_TOOL_USE not in [e.name for e in seen]


def test_updated_input_is_applied(tmp_path: Path) -> None:
    (tmp_path / "conv-hooks").mkdir(exist_ok=True)
    toolset, _, workspace = _toolset(tmp_path, lambda e: LifecycleDecision(updated_input={"file_path": "b.txt"}) if e.name is E.PRE_TOOL_USE else None)
    (workspace.root / "b.txt").write_text("from b\n", encoding="utf-8")
    outcome = toolset.invoke("read_file", {"path": "a.txt"})
    assert outcome.status == "succeeded" and "from b" in outcome.content


def test_context_and_post_feedback_are_appended(tmp_path: Path) -> None:
    def decide(e):
        if e.name is E.PRE_TOOL_USE:
            return LifecycleDecision(additional_context=["pre-note"])
        if e.name is E.POST_TOOL_USE:
            return LifecycleDecision(blocked=True, reason="lint failed", additional_context=["post-note"])
        return None

    toolset, _, _ = _toolset(tmp_path, decide)
    outcome = toolset.invoke("read_file", {"path": "a.txt"})
    assert outcome.status == "succeeded"
    assert "<system-reminder>" in outcome.content and "pre-note" in outcome.content and "post-note" in outcome.content and "lint failed" in outcome.content


def test_stop_request(tmp_path: Path) -> None:
    toolset, _, _ = _toolset(tmp_path, lambda e: LifecycleDecision(stop=True, stop_reason="halt") if e.name is E.PRE_TOOL_USE else None)
    outcome = toolset.invoke("read_file", {"path": "a.txt"})
    assert outcome.error_code == "HOOK_STOPPED" and toolset.stop_requested == "halt"


def test_hook_allow_skips_prompt_but_not_deny_rules(tmp_path: Path) -> None:
    requests = []
    toolset, _, _ = _toolset(
        tmp_path, lambda e: LifecycleDecision(permission_decision="allow") if e.name is E.PRE_TOOL_USE else None,
        code_mode_active=True, permission_mode="ask_permissions", interactive_permissions=True, on_permission_request=requests.append,
    )
    outcome = toolset.invoke("run_command", {"command": "echo hi"})
    assert outcome.status == "succeeded" and requests == []


def test_hook_ask_forces_a_human(tmp_path: Path) -> None:
    toolset, _, _ = _toolset(tmp_path, lambda e: LifecycleDecision(permission_decision="ask") if e.name is E.PRE_TOOL_USE else None, code_mode_active=True, permission_mode="full_autonomy")
    outcome = toolset.invoke("run_command", {"command": "echo hi"})
    assert outcome.status == "failed" and outcome.error_code == "CODE_PERMISSION_DENIED"


def test_permission_request_hook_can_approve_without_ui(tmp_path: Path) -> None:
    def decide(e):
        if e.name is E.PERMISSION_REQUEST:
            return LifecycleDecision(permission_decision="allow")
        return None

    requests = []
    toolset, seen, _ = _toolset(tmp_path, decide, code_mode_active=True, permission_mode="ask_permissions", interactive_permissions=True, on_permission_request=requests.append)
    outcome = toolset.invoke("run_command", {"command": "echo hi"})
    assert outcome.status == "succeeded" and requests == []
    assert E.PERMISSION_REQUEST in [e.name for e in seen]


def test_permission_request_hook_deny(tmp_path: Path) -> None:
    toolset, _, _ = _toolset(tmp_path, lambda e: LifecycleDecision(permission_decision="deny", reason="ci only") if e.name is E.PERMISSION_REQUEST else None, code_mode_active=True, permission_mode="ask_permissions", interactive_permissions=True)
    outcome = toolset.invoke("run_command", {"command": "echo hi"})
    assert outcome.error_code == "CODE_PERMISSION_DENIED" and "ci only" in outcome.content


def test_lifecycle_context_and_extra_env(tmp_path: Path) -> None:
    import sys

    toolset, seen, _ = _toolset(tmp_path, lambda e: None, lifecycle_context={"agent_id": "agent-7", "agent_type": "Explore"}, extra_env={"ORIN_TEST_VAR": "from-hook"})
    toolset.invoke("read_file", {"path": "a.txt"})
    assert dict(seen[0].payload)["agent_id"] == "agent-7" and dict(seen[0].payload)["agent_type"] == "Explore"
    outcome = toolset.invoke("run_command", {"command": f'"{sys.executable}" -c "import os; print(os.environ[\'ORIN_TEST_VAR\'])"'})
    assert "from-hook" in outcome.content
