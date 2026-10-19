"""W5: a plugin's hooks run in the settings hooks' dispatcher — every event, real decisions, the person's consent."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.hooks.config import non_managed_hooks_allowed
from agentos.hooks.dispatcher import HookDispatcher
from agentos.hooks.runners import CommandHookRunner
from agentos.plugins.hook_specs import plugin_hook_specs
from agentos.plugins.hooks_manifest import parse_hooks
from agentos.plugins.runtime import active_plugins
from agentos.settings.loader import EffectiveSettings, SettingsLayer

GUARD = """
import json, os, sys
event = json.load(sys.stdin)
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse", "permissionDecision": "deny",
    "permissionDecisionReason": "guard says no to " + event.get("tool_name", "?") + " in " + os.environ.get("ORIN_PLUGIN_OPTION_REGION", "?")
        + (" with a key" if os.environ.get("CLAUDE_PLUGIN_OPTION_API_KEY") else ""),
}}))
"""


def _plugin(root: Path, hooks: dict) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({
        "name": "kit", "version": "1.0.0",
        "userConfig": {"region": {"description": "r"}, "api_key": {"description": "k", "sensitive": True}},
    }), encoding="utf-8")
    (root / "hooks").mkdir()
    (root / "hooks" / "hooks.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    (root / "hooks" / "guard.py").write_text(GUARD, encoding="utf-8")
    return root


def _guard_hooks() -> dict:
    command = f'"{sys.executable}" ${{CLAUDE_PLUGIN_ROOT}}/hooks/guard.py'
    return {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": command, "timeout": 30}]}],
        "Stop": [{"hooks": [{"type": "command", "command": command + " ${user_config.api_key}"}]}],
    }


class _Plugins:
    def __init__(self, rows):
        self.rows = rows

    def list(self, user_id):
        return list(self.rows)


def _row(root: Path, enabled_ids: list[str]) -> dict:
    return {
        "plugin_id": "kit", "state": "active", "install_path": str(root),
        "contributions": [{"kind": "hook", "reference": hook_id, "enabled": True} for hook_id in enabled_ids],
    }


def test_every_event_is_listed_with_its_async_flag_and_timeout(tmp_path: Path) -> None:
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x", "async": True, "timeout": 900}]}],
        "NotAnEvent": [{"hooks": [{"type": "command", "command": "x"}]}],
    }}), encoding="utf-8")
    hooks, warnings = parse_hooks(tmp_path / "hooks", plugin_id="kit")
    assert [(item.hook_id, item.run_async, item.timeout_seconds) for item in hooks] == [("kit:PreToolUse:0", True, 600)]
    assert any("NotAnEvent" in warning for warning in warnings)


def test_only_the_hooks_the_person_allowed_become_specs(tmp_path: Path) -> None:
    root = _plugin(tmp_path / "kit", _guard_hooks())
    plugins, _ = active_plugins(_Plugins([_row(root, ["kit:PreToolUse:0", "kit:Stop:0"])]), "u1", {})
    specs, errors = plugin_hook_specs(plugins, user_id="u1", option_values=lambda _pid: {"region": "eu", "api_key": "s3cret"})
    assert [(spec.event, spec.source) for spec in specs] == [(HookEvent.PRE_TOOL_USE, "plugin:kit")]
    assert any("sensitive" in error.message for error in errors)  # the Stop hook named the sensitive option
    environment = dict(specs[0].plugin_env)
    assert environment["ORIN_PLUGIN_OPTION_REGION"] == "eu" and environment["CLAUDE_PLUGIN_ROOT"] == str(root)
    later, errors = plugin_hook_specs(active_plugins(_Plugins([_row(root, ["kit:Stop:0"])]), "u1", {})[0], user_id="u1")
    assert later == () and any("consent" in error.message for error in errors)


def test_a_plugin_hook_can_deny_a_tool_call(tmp_path: Path) -> None:
    root = _plugin(tmp_path / "kit", _guard_hooks())
    plugins, _ = active_plugins(_Plugins([_row(root, ["kit:PreToolUse:0"])]), "u1", {})
    specs, _ = plugin_hook_specs(plugins, user_id="u1", option_values=lambda _pid: {"region": "eu", "api_key": "k"})
    bus = LifecycleBus()
    bus.subscribe(HookDispatcher(specs, command_runner=CommandHookRunner(tmp_path / "project")))
    (tmp_path / "project").mkdir()
    decision = bus.emit(HookEvent.PRE_TOOL_USE, tool_name="Bash", tool_input={"command": "ls"})
    assert decision.permission_decision == "deny" and decision.reason == "guard says no to Bash in eu with a key"
    assert bus.emit(HookEvent.PRE_TOOL_USE, tool_name="Read", tool_input={}).permission_decision is None


def test_a_command_outside_the_plugin_is_refused(tmp_path: Path) -> None:
    root = _plugin(tmp_path / "kit", {"PreToolUse": [{"hooks": [{"type": "command", "command": f'"{sys.executable}" -c "print(1)"'}]}]})
    plugins, _ = active_plugins(_Plugins([_row(root, ["kit:PreToolUse:0"])]), "u1", {})
    specs, _ = plugin_hook_specs(plugins, user_id="u1")
    run = CommandHookRunner(tmp_path).run(specs[0], {"tool_name": "Bash"})
    assert run.exit_code is None and "refused" in str(run.error)


def test_managed_switches_turn_plugin_hooks_off() -> None:
    def settings(managed: dict, user: dict | None = None) -> EffectiveSettings:
        layers = (SettingsLayer("user", None, user or {}), SettingsLayer("managed", None, managed))
        return EffectiveSettings({}, {}, layers, (), False)

    assert non_managed_hooks_allowed(settings({})) is True
    assert non_managed_hooks_allowed(settings({"allowManagedHooksOnly": True})) is False
    assert non_managed_hooks_allowed(settings({"disableAllHooks": True})) is False
    assert non_managed_hooks_allowed(settings({}, {"disableAllHooks": True})) is False


def test_the_turn_installs_the_persons_plugin_hooks(tmp_path: Path) -> None:
    from tests.unit.agentic.test_disk_skills_session import _session

    root = _plugin(tmp_path / "kit", _guard_hooks())
    session = _session(tmp_path, "hi")
    session.plugin_service = _Plugins([_row(root, ["kit:PreToolUse:0"])])
    session.build_runtime()
    assert session._hook_dispatcher is None  # this Orin runs no plugin hooks unless its capabilities say so
    session = _session(tmp_path, "hi")
    session.plugin_service = _Plugins([_row(root, ["kit:PreToolUse:0"])])
    session.plugin_hooks = True
    session.build_runtime()
    assert [spec.source for spec in session._hook_dispatcher.specs] == ["plugin:kit"]
