from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.lifecycle import HookEvent
from agentos.hooks.config import MAX_TIMEOUT, load_hook_specs
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_managed_dir, orin_user_dir


def _user(data: dict) -> None:
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "settings.json").write_text(json.dumps(data), encoding="utf-8")


def _managed(data: dict) -> None:
    orin_managed_dir().mkdir(parents=True, exist_ok=True)
    (orin_managed_dir() / "managed-settings.json").write_text(json.dumps(data), encoding="utf-8")


def test_parses_all_handler_types_with_defaults_and_clamps() -> None:
    _user({"hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "echo pre", "timeout": 5},
            {"type": "http", "url": "https://hooks.example.com/x", "headers": {"Authorization": "Bearer $TOKEN"}, "allowedEnvVars": ["TOKEN"]},
            {"type": "prompt", "prompt": "Is $ARGUMENTS safe?", "model": "fast"},
            {"type": "agent", "prompt": "Verify tests", "timeout": 9999},
        ]}],
        "Stop": [{"hooks": [{"command": "echo stop", "async": True, "onFailure": "block", "statusMessage": "checking"}]}],
    }})
    specs, errors = load_hook_specs(load_effective_settings(None))
    assert errors == ()
    by_type = {spec.type: spec for spec in specs if spec.event is HookEvent.PRE_TOOL_USE}
    assert by_type["command"].timeout == 5 and by_type["command"].matcher == "Bash" and by_type["command"].source == "user"
    assert by_type["http"].timeout == 30 and dict(by_type["http"].headers) == {"Authorization": "Bearer $TOKEN"} and by_type["http"].allowed_env_vars == ("TOKEN",)
    assert by_type["prompt"].model == "fast" and by_type["prompt"].timeout == 30
    assert by_type["agent"].timeout == MAX_TIMEOUT
    stop = next(spec for spec in specs if spec.event is HookEvent.STOP)
    assert stop.type == "command" and stop.run_async and stop.on_failure == "block" and stop.status_message == "checking" and stop.timeout == 60


def test_invalid_entries_are_reported_not_fatal() -> None:
    _user({"hooks": {
        "NotAnEvent": [{"hooks": [{"command": "x"}]}],
        "PreToolUse": [
            {"matcher": "(", "hooks": [{"command": "x"}]},
            {"hooks": [{"type": "command"}, {"type": "weird", "command": "x"}, {"type": "http", "url": "ftp://x"}, "nope"]},
            {"hooks": [{"command": "ok"}]},
        ],
        "Stop": "not a list",
    }})
    specs, errors = load_hook_specs(load_effective_settings(None))
    assert [spec.command for spec in specs] == ["ok"]
    assert len(errors) >= 6


def test_precedence_order_and_disable_switches(tmp_path: Path) -> None:
    _user({"hooks": {"Stop": [{"hooks": [{"command": "user"}]}]}})
    _managed({"hooks": {"Stop": [{"hooks": [{"command": "managed"}]}]}})
    specs, _ = load_hook_specs(load_effective_settings(None))
    assert [spec.command for spec in specs] == ["managed", "user"]
    _managed({"allowManagedHooksOnly": True, "hooks": {"Stop": [{"hooks": [{"command": "managed"}]}]}})
    assert [spec.command for spec in load_hook_specs(load_effective_settings(None))[0]] == ["managed"]
    _managed({"disableAllHooks": True})
    assert load_hook_specs(load_effective_settings(None))[0] == ()
    # allowManagedHooksOnly from a user file is ignored
    _managed({})
    _user({"allowManagedHooksOnly": True, "hooks": {"Stop": [{"hooks": [{"command": "user"}]}]}})
    assert [spec.command for spec in load_hook_specs(load_effective_settings(None))[0]] == ["user"]


def test_untrusted_repository_hooks_never_load(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "evil"}]}]}}), encoding="utf-8")
    assert load_hook_specs(load_effective_settings(root, trusted=False))[0] == ()
    trusted = load_hook_specs(load_effective_settings(root, trusted=True))[0]
    assert [spec.command for spec in trusted] == ["evil"] and trusted[0].source == "project"


def test_hook_count_is_capped() -> None:
    _user({"hooks": {"Stop": [{"hooks": [{"command": f"echo {i}"} for i in range(300)]}]}})
    specs, errors = load_hook_specs(load_effective_settings(None))
    assert len(specs) == 200 and any("200" in error.message for error in errors)


def test_untrusted_repository_cannot_switch_off_managed_or_user_hooks(tmp_path: Path) -> None:
    # W2-R3: disableAllHooks from an untrusted repository is ignored and reported; it never silences
    # managed or user hooks.
    _managed({"hooks": {"PreToolUse": [{"hooks": [{"command": "managed-guard"}]}]}})
    _user({"hooks": {"UserPromptSubmit": [{"hooks": [{"command": "user-secret-scanner"}]}]}})
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "settings.json").write_text(json.dumps({"disableAllHooks": True}), encoding="utf-8")
    untrusted = load_effective_settings(root, trusted=False)
    specs, _ = load_hook_specs(untrusted)
    assert [spec.command for spec in specs] == ["managed-guard", "user-secret-scanner"]
    assert "disableAllHooks" in untrusted.untrusted_ignored


def test_trusted_repository_switch_disables_repository_and_user_hooks_but_not_managed(tmp_path: Path) -> None:
    _managed({"hooks": {"PreToolUse": [{"hooks": [{"command": "managed-guard"}]}]}})
    _user({"hooks": {"UserPromptSubmit": [{"hooks": [{"command": "user-secret-scanner"}]}]}})
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "settings.json").write_text(json.dumps({
        "disableAllHooks": True,
        "hooks": {"Stop": [{"hooks": [{"command": "repo-stop"}]}]},
    }), encoding="utf-8")
    specs, _ = load_hook_specs(load_effective_settings(root, trusted=True))
    assert [spec.command for spec in specs] == ["managed-guard"]


def test_user_switch_disables_user_hooks_but_not_managed(tmp_path: Path) -> None:
    _managed({"hooks": {"PreToolUse": [{"hooks": [{"command": "managed-guard"}]}]}})
    _user({"disableAllHooks": True, "hooks": {"UserPromptSubmit": [{"hooks": [{"command": "user-secret-scanner"}]}]}})
    specs, _ = load_hook_specs(load_effective_settings(None))
    assert [spec.command for spec in specs] == ["managed-guard"]
