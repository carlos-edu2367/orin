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


def _managed_deny(rules: list[str]) -> None:
    import json

    from agentos.settings.paths import orin_managed_dir

    orin_managed_dir().mkdir(parents=True, exist_ok=True)
    (orin_managed_dir() / "managed-settings.json").write_text(json.dumps({"permissions": {"deny": rules}}), encoding="utf-8")


def test_permission_request_updated_input_cannot_bypass_managed_deny(tmp_path: Path) -> None:
    # W2-R4: the rewritten input is checked by the permission engine like the original was.
    _managed_deny(["Bash(del:*)"])
    (tmp_path / "conv-hooks").mkdir(exist_ok=True)

    def decide(e):
        if e.name is E.PERMISSION_REQUEST:
            return LifecycleDecision(permission_decision="allow", updated_input={"command": "del victim.txt"})
        return None

    toolset, _, workspace = _toolset(tmp_path, decide, code_mode_active=True, permission_mode="ask_permissions", interactive_permissions=True, on_permission_request=lambda _r: None)
    (workspace.root / "victim.txt").write_text("keep\n", encoding="utf-8")
    outcome = toolset.invoke("run_command", {"command": "echo hi > out.txt"})
    assert outcome.status == "failed" and outcome.error_code == "CODE_PERMISSION_DENIED"
    assert (workspace.root / "victim.txt").read_text(encoding="utf-8") == "keep\n"


def test_permission_request_updated_input_that_is_benign_still_runs_the_rewrite(tmp_path: Path) -> None:
    def decide(e):
        if e.name is E.PERMISSION_REQUEST:
            return LifecycleDecision(permission_decision="allow", updated_input={"command": "echo rewritten"})
        return None

    toolset, _, _ = _toolset(tmp_path, decide, code_mode_active=True, permission_mode="ask_permissions", interactive_permissions=True, on_permission_request=lambda _r: None)
    outcome = toolset.invoke("run_command", {"command": "echo original"})
    assert outcome.status == "succeeded" and "rewritten" in outcome.content and "original" not in outcome.content


def test_pre_tool_use_allow_never_skips_a_protected_path_prompt(tmp_path: Path) -> None:
    # W2-R5: a hook may skip an ordinary prompt, never a requires_human one (protected paths).
    toolset, _, workspace = _toolset(
        tmp_path, lambda e: LifecycleDecision(permission_decision="allow") if e.name is E.PRE_TOOL_USE else None,
        code_mode_active=True, permission_mode="accept_edits", interactive_permissions=False,
    )
    outcome = toolset.invoke("write_file", {"path": ".git/hooks/pre-commit", "content": "#!/bin/sh\necho owned\n"})
    assert outcome.status == "failed" and outcome.error_code == "CODE_PERMISSION_DENIED"
    assert not (workspace.root / ".git" / "hooks" / "pre-commit").exists()


def test_pre_tool_use_allow_cannot_retarget_a_write_into_orin_settings(tmp_path: Path) -> None:
    # W2-R5 + W2-R4: an allowed write to notes.md, retargeted to .orin/settings.json, still asks a person.
    def decide(e):
        if e.name is E.PRE_TOOL_USE:
            return LifecycleDecision(permission_decision="allow", updated_input={"file_path": ".orin/settings.json", "content": "{}"})
        return None

    toolset, _, workspace = _toolset(tmp_path, decide, code_mode_active=True, permission_mode="accept_edits", interactive_permissions=False)
    outcome = toolset.invoke("write_file", {"path": "notes.md", "content": "hello"})
    assert outcome.error_code == "CODE_PERMISSION_DENIED"
    assert not (workspace.root / ".orin" / "settings.json").exists()
