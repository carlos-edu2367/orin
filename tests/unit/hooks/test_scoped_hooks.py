"""W5: hooks declared by a skill or an agent run only for the agent that uses it."""
from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.hooks.config import parse_hook_mapping
from agentos.settings.paths import orin_managed_dir, orin_user_dir
from agentos.skills.disk import load_disk_skills


def test_a_hooks_object_parses_on_its_own() -> None:
    specs, errors = parse_hook_mapping({
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}],
        "Nope": [],
    }, "skill:x")
    assert [(spec.event, spec.source, spec.command) for spec in specs] == [(HookEvent.PRE_TOOL_USE, "skill:x", "echo hi")]
    assert any("Nope" in error.message for error in errors)


def _session(tmp_path: Path):
    from tests.unit.agentic.test_disk_skills_session import _session as make

    session = make(tmp_path, "hi")
    session.lifecycle = LifecycleBus()
    session.build_runtime()
    return session


class _Runner:
    """Stands in for the command runner: records what ran and denies."""

    def __init__(self) -> None:
        self.ran: list[dict] = []

    def run(self, spec, payload):
        from agentos.hooks.protocol import HookRun

        self.ran.append({"source": spec.source, "event": spec.event, **dict(payload)})
        return HookRun(0, json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}}), "")


def test_a_skills_hooks_run_for_the_agent_that_used_it(tmp_path: Path, monkeypatch) -> None:
    import agentos.agentic.session as session_module

    runner = _Runner()
    monkeypatch.setattr(session_module, "CommandHookRunner", lambda *args, **kwargs: runner)
    folder = orin_user_dir() / "skills" / "careful"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\ndescription: Be careful\nhooks:\n  PreToolUse:\n    - matcher: Bash\n      hooks:\n        - type: command\n          command: guard\n---\nBe careful.",
        encoding="utf-8",
    )
    session = _session(tmp_path)
    skill = load_disk_skills(workspace_root=None, trusted=False).get("careful")
    assert session.lifecycle.emit(HookEvent.PRE_TOOL_USE, tool_name="Bash").permission_decision is None  # not used yet
    session._skill_hooks(skill, None)
    session._skill_hooks(skill, None)  # a second use does not add the hooks twice
    assert session.lifecycle.emit(HookEvent.PRE_TOOL_USE, tool_name="Bash").permission_decision == "deny"
    assert session.lifecycle.emit(HookEvent.PRE_TOOL_USE, tool_name="Bash", agent_id="sub-1").permission_decision is None
    assert [item["source"] for item in runner.ran] == ["skill:careful"]


def test_an_agents_hooks_run_during_its_run_and_its_stop_becomes_subagent_stop(tmp_path: Path, monkeypatch) -> None:
    import agentos.agentic.session as session_module
    from agentos.agentic.agent_definitions import load_agent_definitions

    runner = _Runner()
    monkeypatch.setattr(session_module, "CommandHookRunner", lambda *args, **kwargs: runner)
    folder = orin_user_dir() / "agents"
    folder.mkdir(parents=True)
    (folder / "guarded.md").write_text(
        "---\nname: guarded\ndescription: Guarded\nhooks:\n  PreToolUse:\n    - matcher: Bash\n      hooks:\n        - {type: command, command: guard}\n"
        "  Stop:\n    - hooks:\n        - {type: command, command: done}\n---\nWork.",
        encoding="utf-8",
    )
    session = _session(tmp_path)
    session._agent_definitions = load_agent_definitions(workspace_root=None, trusted=False)
    seen: list[str] = []

    def fake_run(**kwargs):
        from agentos.agentic.tools._shared import ToolOutcome

        decision = session.lifecycle.emit(HookEvent.PRE_TOOL_USE, tool_name="Bash", agent_id=kwargs["agent_id"])
        seen.append(str(decision.permission_decision))
        return ToolOutcome("succeeded", "ok", "done", {})

    monkeypatch.setattr(session, "_execute_subagent_run_inner", fake_run)
    assert session._run_subagent(subagent_type="guarded", task="Go").status == "succeeded"
    assert seen == ["deny"]
    assert [item["event"] for item in runner.ran] == [HookEvent.PRE_TOOL_USE, HookEvent.SUBAGENT_STOP]
    assert session.lifecycle.emit(HookEvent.PRE_TOOL_USE, tool_name="Bash", agent_id=runner.ran[0]["agent_id"]).permission_decision is None


def test_managed_settings_can_switch_skill_hooks_off(tmp_path: Path, monkeypatch) -> None:
    import agentos.agentic.session as session_module

    runner = _Runner()
    monkeypatch.setattr(session_module, "CommandHookRunner", lambda *args, **kwargs: runner)
    orin_managed_dir().mkdir(parents=True, exist_ok=True)
    (orin_managed_dir() / "managed-settings.json").write_text(json.dumps({"allowManagedHooksOnly": True}), encoding="utf-8")
    folder = orin_user_dir() / "skills" / "careful"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\ndescription: Be careful\nhooks:\n  PreToolUse:\n    - hooks:\n        - {type: command, command: guard}\n---\nx", encoding="utf-8",
    )
    session = _session(tmp_path)
    session._skill_hooks(load_disk_skills(workspace_root=None, trusted=False).get("careful"), None)
    assert session.lifecycle.emit(HookEvent.PRE_TOOL_USE, tool_name="Bash").permission_decision is None and runner.ran == []


def test_managed_disable_all_hooks_switches_off_a_managed_skills_hooks(tmp_path: Path, monkeypatch) -> None:
    """Deviation 39 (review of Task 35): managed settings' disableAllHooks reaches a managed skill's hooks too."""
    import agentos.agentic.session as session_module

    runner = _Runner()
    monkeypatch.setattr(session_module, "CommandHookRunner", lambda *args, **kwargs: runner)
    orin_managed_dir().mkdir(parents=True, exist_ok=True)
    (orin_managed_dir() / "managed-settings.json").write_text(json.dumps({"disableAllHooks": True}), encoding="utf-8")
    folder = orin_managed_dir() / "skills" / "careful"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\ndescription: Be careful\nhooks:\n  PreToolUse:\n    - hooks:\n        - {type: command, command: guard}\n---\nx", encoding="utf-8",
    )
    session = _session(tmp_path)
    skill = load_disk_skills(workspace_root=None, trusted=False).get("careful")
    assert skill.origin == "managed"
    session._skill_hooks(skill, None)
    assert session.lifecycle.emit(HookEvent.PRE_TOOL_USE, tool_name="Bash").permission_decision is None and runner.ran == []
