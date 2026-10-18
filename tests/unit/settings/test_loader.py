from __future__ import annotations

import json
import os
from pathlib import Path

from agentos.settings import load_effective_settings, merge_settings
from agentos.settings.paths import SettingsPaths, claude_user_dir, orin_managed_dir, orin_user_dir


def _write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def test_merge_scalars_objects_and_lists() -> None:
    base = {"model": "a", "env": {"A": "1", "B": "2"}, "permissions": {"allow": ["Read", "Bash(ls)"]}}
    overlay = {"model": "b", "env": {"B": "3"}, "permissions": {"allow": ["Bash(ls)", "Edit"]}}
    merged = merge_settings(base, overlay)
    assert merged == {"model": "b", "env": {"A": "1", "B": "3"}, "permissions": {"allow": ["Bash(ls)", "Edit", "Read"]}}
    assert base["permissions"]["allow"] == ["Read", "Bash(ls)"]  # inputs untouched


def test_precedence_managed_cli_local_project_user(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(orin_user_dir() / "settings.json", {"model": "user", "a": "user", "b": "user", "c": "user", "d": "user"})
    _write(project / ".orin" / "settings.json", {"a": "project", "b": "project", "c": "project", "d": "project"})
    _write(project / ".orin" / "settings.local.json", {"b": "local", "c": "local", "d": "local"})
    _write(orin_managed_dir() / "managed-settings.json", {"d": "managed"})
    settings = load_effective_settings(project, cli={"c": "cli", "d": "cli"})
    assert settings.get("model") == "user"
    assert settings.get("a") == "project"
    assert settings.get("b") == "local"
    assert settings.get("c") == "cli"
    assert settings.get("d") == "managed"
    assert settings.provenance["model"] == "user"
    assert settings.provenance["a"] == "project"
    assert settings.provenance["d"] == "managed"


def test_orin_beats_claude_in_the_same_layer_and_both_are_read(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(project / ".claude" / "settings.json", {"model": "claude", "outputStyle": "Explanatory", "permissions": {"allow": ["Read"]}})
    _write(project / ".orin" / "settings.json", {"model": "orin", "permissions": {"allow": ["Edit"]}})
    _write(claude_user_dir() / "settings.json", {"permissions": {"deny": ["Bash(rm -rf *)"]}})
    settings = load_effective_settings(project)
    assert settings.get("model") == "orin"
    assert settings.get("outputStyle") == "Explanatory"
    assert set(settings.get("permissions.allow")) == {"Read", "Edit"}
    assert settings.get("permissions.deny") == ["Bash(rm -rf *)"]


def test_managed_dropins_are_sorted_and_dotfiles_ignored() -> None:
    managed = orin_managed_dir()
    _write(managed / "managed-settings.json", {"x": "base"})
    _write(managed / "managed-settings.d" / "20-b.json", {"x": "b"})
    _write(managed / "managed-settings.d" / "10-a.json", {"x": "a", "y": "a"})
    _write(managed / "managed-settings.d" / ".hidden.json", {"x": "hidden"})
    _write(managed / "managed-settings.d" / "notes.txt", "ignored")
    settings = load_effective_settings(None)
    assert settings.get("x") == "b"
    assert settings.get("y") == "a"


def test_invalid_files_are_reported_and_skipped(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(project / ".orin" / "settings.json", "{not json")
    _write(project / ".orin" / "settings.local.json", "[1, 2]")
    _write(orin_user_dir() / "settings.json", {"model": "ok"})
    settings = load_effective_settings(project)
    assert settings.get("model") == "ok"
    paths = {Path(error.path).name for error in settings.errors}
    assert paths == {"settings.json", "settings.local.json"}


def test_oversized_file_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(project / ".orin" / "settings.json", '{"pad": "' + ("x" * (1024 * 1024 + 10)) + '"}')
    settings = load_effective_settings(project)
    assert settings.get("pad") is None
    assert settings.errors


def test_project_layers_cannot_inject_privileged_keys(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(project / ".claude" / "settings.json", {
        "claudeMd": "evil", "allowedMcpServers": [{"serverName": "x"}], "memory": "/etc",
        "permissions": {"allowBypassPermissionsMode": True, "allow": ["Bash(*)"], "deny": ["Read(.env)"]},
    })
    _write(orin_user_dir() / "settings.json", {"disableAllHooks": True})
    _write(project / ".orin" / "settings.local.json", {"disableAllHooks": False})
    settings = load_effective_settings(project, trusted=True)
    assert settings.get("claudeMd") is None
    assert settings.get("allowedMcpServers") is None
    assert settings.get("memory") is None
    assert settings.get("permissions.allowBypassPermissionsMode") is None
    assert settings.get("disableAllHooks") is True
    assert settings.get("permissions.allow") == ["Bash(*)"]  # trusted workspace keeps allow rules
    assert settings.get("permissions.deny") == ["Read(.env)"]


def test_untrusted_workspace_drops_executable_and_widening_keys(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(project / ".orin" / "settings.json", {
        "hooks": {"PreToolUse": []}, "apiKeyHelper": "x", "statusLine": {"type": "command"}, "env": {"A": "1"},
        "permissions": {"allow": ["Bash(*)"], "ask": ["Edit"], "deny": ["Read(.env)"], "additionalDirectories": ["/"]},
        "enableAllProjectMcpServers": True, "model": "kept",
    })
    settings = load_effective_settings(project, trusted=False)
    for key in ("hooks", "apiKeyHelper", "statusLine", "env", "permissions.allow", "permissions.additionalDirectories", "enableAllProjectMcpServers"):
        assert settings.get(key) is None, key
    assert settings.get("permissions.ask") == ["Edit"]
    assert settings.get("permissions.deny") == ["Read(.env)"]
    assert settings.get("model") == "kept"
    assert settings.trusted is False


def test_setting_sources_filter_layers(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(orin_user_dir() / "settings.json", {"u": 1})
    _write(project / ".orin" / "settings.json", {"p": 1})
    _write(project / ".orin" / "settings.local.json", {"l": 1})
    _write(orin_managed_dir() / "managed-settings.json", {"m": 1})
    settings = load_effective_settings(project, sources={"project"}, cli={"c": 1})
    assert settings.get("u") is None and settings.get("l") is None
    assert settings.get("p") == 1 and settings.get("m") == 1 and settings.get("c") == 1


def test_experimental_flags_and_permission_rules_with_sources(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(orin_user_dir() / "settings.json", {"experimental": {"claudeToolNames": True}, "permissions": {"allow": ["Read", "Edit"]}})
    _write(project / ".orin" / "settings.json", {"experimental": {"other": "yes"}, "permissions": {"allow": ["Read", "Bash(npm test)"]}})
    settings = load_effective_settings(project)
    assert settings.experimental("claudeToolNames") is True
    assert settings.experimental("other") is False  # non-bool is not a flag
    assert settings.experimental("missing") is False
    assert settings.permission_rules("allow") == (("Read", "project"), ("Bash(npm test)", "project"), ("Edit", "user"))
    assert settings.permission_rules("deny") == ()


def test_candidate_files_include_missing_ones(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    settings = load_effective_settings(project)
    names = {str(path) for path in settings.candidate_files}
    assert str(project / ".orin" / "settings.json") in names
    assert str(project / ".claude" / "settings.local.json") in names
    assert str(orin_user_dir() / "settings.json") in names


def test_writable_files_are_orin_files(tmp_path: Path) -> None:
    paths = SettingsPaths(tmp_path)
    assert paths.writable_file("project") == tmp_path / ".orin" / "settings.json"
    assert paths.writable_file("local") == tmp_path / ".orin" / "settings.local.json"
    assert paths.writable_file("user") == orin_user_dir() / "settings.json"


def test_user_dir_env_override_is_respected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ORIN_USER_DIR", str(tmp_path / "custom"))
    assert orin_user_dir() == tmp_path / "custom"
    monkeypatch.delenv("ORIN_USER_DIR")
    assert orin_user_dir() == Path.home() / ".orin"
    assert os.environ.get("ORIN_USER_DIR") is None
