"""W6-R4: a policy that disables bypass mode is read from every layer, and no lower layer can lift it."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agentos.agentic.loop_settings import bypass_disabled
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_managed_dir, orin_user_dir


def _write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _layers(*pairs: tuple[str, object]) -> SimpleNamespace:
    return SimpleNamespace(layers=[SimpleNamespace(name=name, data=data) for name, data in pairs])


def test_no_layer_means_bypass_is_allowed() -> None:
    assert bypass_disabled(_layers()) is False
    assert bypass_disabled(_layers(("user", {"permissions": {"allow": ["Bash(ls)"]}}))) is False


def test_disable_in_any_layer_disables_bypass() -> None:
    disable = {"permissions": {"disableBypassPermissionsMode": "disable"}}
    for name in ("managed", "user", "cli", "project"):
        assert bypass_disabled(_layers((name, disable))) is True, name


def test_only_the_disable_value_counts() -> None:
    for value in ("enable", "false", True, None, {}):
        assert bypass_disabled(_layers(("managed", {"permissions": {"disableBypassPermissionsMode": value}}))) is False


def test_a_lower_layer_cannot_lift_a_managed_disable() -> None:
    settings = _layers(
        ("managed", {"permissions": {"disableBypassPermissionsMode": "disable"}}),
        ("user", {"permissions": {"disableBypassPermissionsMode": "enable"}}),
        ("project", {"permissions": {"allowBypassPermissionsMode": True}}),
    )
    assert bypass_disabled(settings) is True


def test_managed_policy_file_disables_bypass_through_the_loader(tmp_path: Path) -> None:
    _write(orin_managed_dir() / "managed-settings.json", {"permissions": {"disableBypassPermissionsMode": "disable"}})
    assert bypass_disabled(load_effective_settings(tmp_path, trusted=False)) is True


def test_user_setting_disables_bypass_through_the_loader(tmp_path: Path) -> None:
    _write(orin_user_dir() / "settings.json", {"permissions": {"disableBypassPermissionsMode": "disable"}})
    assert bypass_disabled(load_effective_settings(tmp_path, trusted=False)) is True


def test_repository_restriction_disables_bypass_when_trusted(tmp_path: Path) -> None:
    _write(tmp_path / ".orin" / "settings.json", {"permissions": {"disableBypassPermissionsMode": "disable"}})
    assert bypass_disabled(load_effective_settings(tmp_path, trusted=True)) is True


def test_repository_cannot_lift_a_managed_disable(tmp_path: Path) -> None:
    _write(orin_managed_dir() / "managed-settings.json", {"permissions": {"disableBypassPermissionsMode": "disable"}})
    _write(tmp_path / ".orin" / "settings.json", {"permissions": {"disableBypassPermissionsMode": "enable"}})
    assert bypass_disabled(load_effective_settings(tmp_path, trusted=True)) is True


def test_repository_cannot_enable_bypass(tmp_path: Path) -> None:
    _write(tmp_path / ".orin" / "settings.json", {"permissions": {"allowBypassPermissionsMode": True}})
    assert bypass_disabled(load_effective_settings(tmp_path, trusted=True)) is False
