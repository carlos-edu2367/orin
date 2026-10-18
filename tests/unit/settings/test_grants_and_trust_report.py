from __future__ import annotations

import json
from pathlib import Path

from agentos.settings import load_effective_settings
from agentos.settings.paths import SettingsPaths, orin_user_dir, workspace_grants_file


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_grants_file_is_user_owned_and_stable(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    grants = workspace_grants_file(root)
    assert grants.is_relative_to(orin_user_dir() / "workspaces")
    assert grants.name == "settings.local.json"
    assert grants == workspace_grants_file(root / "." )
    assert SettingsPaths(root).writable_file("local") == grants
    assert SettingsPaths(root).files_for("local")[-1] == grants


def test_grants_apply_even_when_untrusted(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(workspace_grants_file(root), {"permissions": {"allow": ["Bash(npm test:*)"]}})
    _write(root / ".orin" / "settings.local.json", {"permissions": {"allow": ["Bash(*)"]}})
    settings = load_effective_settings(root, trusted=False)
    assert ("Bash(npm test:*)", "local") in settings.permission_rules("allow")
    assert all(rule != "Bash(*)" for rule, _ in settings.permission_rules("allow"))


def test_untrusted_ignored_lists_dropped_repository_keys(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / ".claude" / "settings.json", {"hooks": {"PreToolUse": []}, "permissions": {"allow": ["Bash(*)"], "deny": ["Read(.env)"]}})
    _write(root / ".orin" / "settings.local.json", {"env": {"A": "1"}})
    untrusted = load_effective_settings(root, trusted=False)
    assert untrusted.untrusted_ignored == ("env", "hooks", "permissions.allow")
    assert load_effective_settings(root, trusted=True).untrusted_ignored == ()
    # keys that are not set are not reported
    _write(root / ".claude" / "settings.json", {"model": "x"})
    _write(root / ".orin" / "settings.local.json", {})
    assert load_effective_settings(root, trusted=False).untrusted_ignored == ()
