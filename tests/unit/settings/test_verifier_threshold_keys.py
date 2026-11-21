"""Tests for verifier threshold setting keys (WS-A3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.agentic.change_measure import (
    DEFAULT_IGNORE_GLOBS,
    DEFAULT_SENSITIVE_PATHS,
    DEFAULT_THRESHOLD_FILES,
    DEFAULT_THRESHOLD_LINES,
)
from agentos.settings.loader import EffectiveSettings, SettingsLayer, empty_settings, load_effective_settings
from agentos.settings.paths import orin_managed_dir, orin_user_dir
from agentos.settings.registry import REGISTRY, ResolvedSetting, Scope, resolve, validate_change


def _synthetic(*layers: tuple[str, dict]) -> EffectiveSettings:
    built = tuple(SettingsLayer(name, None, data) for name, data in layers)
    return EffectiveSettings(data={}, provenance={}, layers=built, errors=(), trusted=True)


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_verifier_threshold_keys_registered_with_spec_defaults_and_scope() -> None:
    expected = {
        "orchestration.verifier.threshold.files": (DEFAULT_THRESHOLD_FILES, "number", 1, 1000),
        "orchestration.verifier.threshold.lines": (DEFAULT_THRESHOLD_LINES, "number", 1, 100000),
        "orchestration.verifier.threshold.sensitivePaths": (list(DEFAULT_SENSITIVE_PATHS), "string_list", None, None),
        "orchestration.verifier.threshold.ignore": (list(DEFAULT_IGNORE_GLOBS), "string_list", None, None),
    }
    empty = empty_settings()
    for path, (default_value, key_type, minimum, maximum) in expected.items():
        assert path in REGISTRY, f"missing key {path}"
        key = REGISTRY[path]
        assert key.scope is Scope.ANY
        assert key.type == key_type
        assert key.minimum == minimum
        assert key.maximum == maximum
        assert key.label.strip()
        assert key.description.strip()
        assert resolve(empty, path) == ResolvedSetting(value=default_value, layer="default", default=default_value)


def test_verifier_threshold_defaults_match_a_rf3_spec() -> None:
    assert DEFAULT_THRESHOLD_FILES == 3
    assert DEFAULT_THRESHOLD_LINES == 150
    assert DEFAULT_SENSITIVE_PATHS == (
        "**/migrations/**",
        "**/alembic/**",
        "**/*.sql",
        "**/auth/**",
        "**/security/**",
        "**/permissions/**",
        ".github/workflows/**",
        "**/Dockerfile*",
        "**/.env*",
    )
    assert DEFAULT_IGNORE_GLOBS == (
        "**/*.lock",
        "**/package-lock.json",
        "**/pnpm-lock.yaml",
        "**/dist/**",
        "**/build/**",
        "**/*.min.*",
        ".orin/**",
    )


def test_verifier_threshold_sensitive_paths_allows_empty_list() -> None:
    assert validate_change("orchestration.verifier.threshold.sensitivePaths", []) == []
    assert validate_change("orchestration.verifier.threshold.ignore", []) == []

    from_user = _synthetic(
        ("user", {"orchestration": {"verifier": {"threshold": {"sensitivePaths": []}}}}),
    )
    resolved = resolve(from_user, "orchestration.verifier.threshold.sensitivePaths")
    assert resolved.value == []
    assert resolved.layer == "user"
    assert resolved.default == list(DEFAULT_SENSITIVE_PATHS)

    from_project = _synthetic(
        ("project", {"orchestration": {"verifier": {"threshold": {"sensitivePaths": []}}}}),
    )
    resolved_project = resolve(from_project, "orchestration.verifier.threshold.sensitivePaths")
    assert resolved_project.value == []
    assert resolved_project.layer == "project"


def test_verifier_threshold_validation_bounds_and_types() -> None:
    assert validate_change("orchestration.verifier.threshold.files", 1) == 1
    assert validate_change("orchestration.verifier.threshold.files", 1000) == 1000
    assert validate_change("orchestration.verifier.threshold.lines", 1) == 1
    assert validate_change("orchestration.verifier.threshold.lines", 100000) == 100000
    assert validate_change("orchestration.verifier.threshold.sensitivePaths", ["**/billing/**"]) == ["**/billing/**"]

    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.threshold.files", 0)
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.threshold.files", 1001)
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.threshold.files", True)
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.threshold.files", 2.5)
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.threshold.lines", 0)
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.threshold.lines", 100001)
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.threshold.sensitivePaths", [""])
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.threshold.sensitivePaths", [123])
    with pytest.raises(ValueError):
        validate_change("orchestration.verifier.threshold.ignore", "*.lock")


def test_verifier_threshold_scope_any_allows_project_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIN_USER_CONFIG_DIR", str(tmp_path / "user"))
    monkeypatch.setenv("ORIN_MANAGED_CONFIG_DIR", str(tmp_path / "managed"))
    project = tmp_path / "repo"

    _write(
        orin_user_dir() / "settings.json",
        {"orchestration": {"verifier": {"threshold": {"files": 5, "lines": 200}}}},
    )
    _write(
        project / ".orin" / "settings.json",
        {
            "orchestration": {
                "verifier": {
                    "threshold": {
                        "files": 4,
                        "sensitivePaths": [],
                        "ignore": ["**/*.generated.py"],
                    }
                }
            }
        },
    )
    _write(orin_managed_dir() / "managed-settings.json", {})

    loaded = load_effective_settings(project, trusted=True)
    assert resolve(loaded, "orchestration.verifier.threshold.files") == ResolvedSetting(4, "project", 3)
    assert resolve(loaded, "orchestration.verifier.threshold.lines") == ResolvedSetting(200, "user", 150)
    assert resolve(loaded, "orchestration.verifier.threshold.sensitivePaths").value == []
    assert resolve(loaded, "orchestration.verifier.threshold.sensitivePaths").layer == "project"
    assert resolve(loaded, "orchestration.verifier.threshold.ignore").value == ["**/*.generated.py"]


def test_verifier_threshold_mutable_defaults_return_fresh_copy() -> None:
    first = resolve(empty_settings(), "orchestration.verifier.threshold.sensitivePaths")
    first.value.append("**/custom/**")
    second = resolve(empty_settings(), "orchestration.verifier.threshold.sensitivePaths")
    assert "**/custom/**" not in second.value
