"""Tests for the layered settings registry, validation and project filter rules (WS-E0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.settings.loader import EffectiveSettings, SettingsLayer, empty_settings, load_effective_settings
from agentos.settings.paths import orin_managed_dir, orin_user_dir
from agentos.settings.registry import REGISTRY, ResolvedSetting, Scope, SettingKey, register, resolve, validate_change


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _synthetic(*layers: tuple[str, dict]) -> EffectiveSettings:
    built = tuple(SettingsLayer(name, None, data) for name, data in layers)
    return EffectiveSettings(data={}, provenance={}, layers=built, errors=(), trusted=True)


def test_package_e_keys_registered_with_spec_defaults_and_scopes() -> None:
    expected = {
        "context.windowOverrides": ({}, Scope.PROJECT_LOWER_ONLY),
        "claudeCli.longContextWithCredits": (False, Scope.USER_ONLY),
        "effortLevel": (None, Scope.USER_ONLY),
        "effortByModel": ({}, Scope.USER_ONLY),
        "effort.boostKeyword": (False, Scope.USER_ONLY),
        "effort.planMode": (None, Scope.USER_ONLY),
        "effort.subagentPolicy": ("model_default", Scope.USER_ONLY),
        "effort.byType": (
            {"explore": "low", "plan": "high", "verifier": "high", "general": "inherit", "fork": "inherit"},
            Scope.USER_ONLY,
        ),
        "effort.maxForSubagents": (None, Scope.PROJECT_LOWER_ONLY),
        "effort.escalateOnFailure": (False, Scope.USER_ONLY),
        "quota.display.enabled": (True, Scope.USER_ONLY),
        "quota.policy.enabled": (False, Scope.USER_ONLY),
        "quota.policy.warnPercent": (80, Scope.USER_ONLY),
        "quota.policy.blockBackgroundPercent": (90, Scope.USER_ONLY),
        "quota.fallback.enabled": (False, Scope.USER_ONLY),
        "quota.fallback.chain": ([], Scope.USER_ONLY),
    }
    empty = empty_settings()
    for path, (default_value, scope) in expected.items():
        assert path in REGISTRY, f"missing key {path}"
        key = REGISTRY[path]
        assert key.scope is scope
        assert key.label.strip()
        assert key.description.strip()
        resolved = resolve(empty, path)
        assert resolved == ResolvedSetting(value=default_value, layer="default", default=default_value)


def test_resolve_returns_fresh_copy_of_mutable_defaults() -> None:
    first = resolve(empty_settings(), "effort.byType")
    first.value["explore"] = "ultra"
    second = resolve(empty_settings(), "effort.byType")
    assert second.value["explore"] == "low"


def test_validate_change_accepts_valid_and_rejects_invalid_values() -> None:
    assert validate_change("quota.display.enabled", False) is False
    assert validate_change("quota.policy.warnPercent", 75) == 75
    assert validate_change("effortLevel", "xhigh") == "xhigh"
    assert validate_change("effortLevel", None) is None
    assert validate_change("effort.subagentPolicy", "by_type") == "by_type"
    assert validate_change("effort.byType", {"explore": "low", "general": "inherit"}) == {
        "explore": "low",
        "general": "inherit",
    }
    assert validate_change("effortByModel", {"anthropic-cli/claude-opus-5-5": "max"}) == {
        "anthropic-cli/claude-opus-5-5": "max",
    }
    assert validate_change("context.windowOverrides", {"anthropic-cli/claude-opus-5-5": 200_000}) == {
        "anthropic-cli/claude-opus-5-5": 200_000,
    }
    assert validate_change("quota.fallback.chain", ["openai-codex/gpt-5", "anthropic-cli/claude-sonnet-5-5"]) == [
        "openai-codex/gpt-5",
        "anthropic-cli/claude-sonnet-5-5",
    ]

    with pytest.raises(ValueError):
        validate_change("unknown.setting.key", True)
    with pytest.raises(ValueError):
        validate_change("quota.display.enabled", "true")
    with pytest.raises(ValueError):
        validate_change("quota.display.enabled", 1)
    with pytest.raises(ValueError):
        validate_change("quota.policy.warnPercent", True)
    with pytest.raises(ValueError):
        validate_change("quota.policy.warnPercent", 0)
    with pytest.raises(ValueError):
        validate_change("quota.policy.warnPercent", 101)
    with pytest.raises(ValueError):
        validate_change("quota.policy.warnPercent", 50.5)
    with pytest.raises(ValueError):
        validate_change("effort.subagentPolicy", "invalid_policy")
    with pytest.raises(ValueError):
        validate_change("effortByModel", {"provider/m": "inherit"})
    with pytest.raises(ValueError):
        validate_change("context.windowOverrides", {"provider/m": 49_999})
    with pytest.raises(ValueError):
        validate_change("context.windowOverrides", {"provider/m": 1_000_001})
    with pytest.raises(ValueError):
        validate_change("quota.fallback.chain", [123])


def test_user_only_ignores_project_and_local_layers() -> None:
    settings = _synthetic(
        ("project", {"quota": {"policy": {"enabled": True, "warnPercent": 10}}}),
        ("local", {"quota": {"policy": {"enabled": True}}}),
    )
    assert resolve(settings, "quota.policy.enabled") == ResolvedSetting(False, "default", False)
    assert resolve(settings, "quota.policy.warnPercent") == ResolvedSetting(80, "default", 80)

    managed = _synthetic(
        ("user", {"quota": {"policy": {"warnPercent": 70}}}),
        ("project", {"quota": {"policy": {"warnPercent": 30}}}),
        ("managed", {"quota": {"policy": {"warnPercent": 85}}}),
    )
    assert resolve(managed, "quota.policy.warnPercent") == ResolvedSetting(85, "managed", 80)


def test_project_off_only_ignores_true_and_accepts_false(monkeypatch: pytest.MonkeyPatch) -> None:
    test_path = "_test.e0.off_only"
    monkeypatch.setitem(
        REGISTRY,
        test_path,
        SettingKey(
            path=test_path,
            type="boolean",
            default=False,
            scope=Scope.PROJECT_OFF_ONLY,
            group="test",
            label="Teste",
            description="Teste",
        ),
    )
    # Repository cannot turn it ON
    repo_on = _synthetic(("project", {"_test": {"e0": {"off_only": True}}}))
    assert resolve(repo_on, test_path) == ResolvedSetting(False, "default", False)

    # Repository CAN turn it OFF when user had it ON
    repo_off = _synthetic(
        ("user", {"_test": {"e0": {"off_only": True}}}),
        ("project", {"_test": {"e0": {"off_only": False}}}),
    )
    assert resolve(repo_off, test_path) == ResolvedSetting(False, "project", False)

    # Managed overrides project
    managed_on = _synthetic(
        ("user", {"_test": {"e0": {"off_only": True}}}),
        ("project", {"_test": {"e0": {"off_only": False}}}),
        ("managed", {"_test": {"e0": {"off_only": True}}}),
    )
    assert resolve(managed_on, test_path) == ResolvedSetting(True, "managed", False)


def test_project_lower_only_for_number_effort_and_map(monkeypatch: pytest.MonkeyPatch) -> None:
    num_path = "_test.e0.max_rounds"
    monkeypatch.setitem(
        REGISTRY,
        num_path,
        SettingKey(
            path=num_path,
            type="number",
            default=3,
            scope=Scope.PROJECT_LOWER_ONLY,
            group="test",
            label="Máximo de rodadas",
            description="Teste numérico",
            minimum=1,
            maximum=10,
        ),
    )
    # Project lowers user value
    lowered = _synthetic(
        ("user", {"_test": {"e0": {"max_rounds": 5}}}),
        ("project", {"_test": {"e0": {"max_rounds": 2}}}),
    )
    assert resolve(lowered, num_path) == ResolvedSetting(2, "project", 3)

    # Project cannot raise user value
    raised = _synthetic(
        ("user", {"_test": {"e0": {"max_rounds": 2}}}),
        ("project", {"_test": {"e0": {"max_rounds": 8}}}),
    )
    assert resolve(raised, num_path) == ResolvedSetting(2, "user", 3)

    # Effort ceiling ranking: low < medium < high < xhigh < max < ultra
    effort_lowered = _synthetic(
        ("user", {"effort": {"maxForSubagents": "high"}}),
        ("project", {"effort": {"maxForSubagents": "medium"}}),
    )
    assert resolve(effort_lowered, "effort.maxForSubagents") == ResolvedSetting("medium", "project", None)

    effort_not_raised = _synthetic(
        ("user", {"effort": {"maxForSubagents": "low"}}),
        ("project", {"effort": {"maxForSubagents": "max"}}),
    )
    assert resolve(effort_not_raised, "effort.maxForSubagents") == ResolvedSetting("low", "user", None)

    effort_managed = _synthetic(
        ("user", {"effort": {"maxForSubagents": "high"}}),
        ("managed", {"effort": {"maxForSubagents": "low"}}),
    )
    assert resolve(effort_managed, "effort.maxForSubagents") == ResolvedSetting("low", "managed", None)

    # Numeric map: context.windowOverrides takes per-key minimum within [50_000, 1_000_000]
    windows = _synthetic(
        ("user", {"context": {"windowOverrides": {"a/m1": 200_000, "a/m2": 300_000}}}),
        ("project", {"context": {"windowOverrides": {"a/m1": 150_000, "a/m2": 500_000, "b/m3": 120_000, "bad/m": 1000}}}),
    )
    resolved_windows = resolve(windows, "context.windowOverrides")
    assert resolved_windows.value == {"a/m1": 150_000, "a/m2": 300_000, "b/m3": 120_000}
    assert resolved_windows.layer == "project"


def test_any_scope_accepts_user_and_project_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    any_path = "_test.e0.threshold_files"
    monkeypatch.setitem(
        REGISTRY,
        any_path,
        SettingKey(
            path=any_path,
            type="number",
            default=3,
            scope=Scope.ANY,
            group="test",
            label="Limiar",
            description="Teste",
            minimum=1,
            maximum=100,
        ),
    )
    user_only = _synthetic(("user", {"_test": {"e0": {"threshold_files": 4}}}))
    assert resolve(user_only, any_path) == ResolvedSetting(4, "user", 3)

    project_overrides = _synthetic(
        ("user", {"_test": {"e0": {"threshold_files": 4}}}),
        ("project", {"_test": {"e0": {"threshold_files": 10}}}),
    )
    assert resolve(project_overrides, any_path) == ResolvedSetting(10, "project", 3)


def test_loader_filters_repository_quota_effort_and_spend_keys(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _write(
        orin_user_dir() / "settings.json",
        {
            "quota": {"policy": {"enabled": False, "warnPercent": 80}},
            "effort": {"maxForSubagents": "high", "subagentPolicy": "inherit"},
            "orchestration": {"verifier": {"enabled": True}},
        },
    )
    _write(
        project / ".orin" / "settings.json",
        {
            "quota": {"policy": {"enabled": True, "warnPercent": 20}, "fallback": {"enabled": True, "chain": ["a/b"]}},
            "effortByModel": {"a/b": "ultra"},
            "claudeCli": {"longContextWithCredits": True},
            "friend": {"sessionDelivery": "queue"},
            "goal": {"defaultMaxTokens": 9999999},
            "pipelines": {"enabled": True, "agentMayStart": True},
            "effort": {
                "boostKeyword": True,
                "planMode": "ultra",
                "subagentPolicy": "by_type",
                "byType": {"explore": "ultra"},
                "escalateOnFailure": True,
                "maxForSubagents": "medium",
                "unknown": "drop-me",
            },
            "orchestration": {
                "verifier": {"enabled": False, "modelPolicy": "explicit", "explicit": "a/b", "effort": "ultra"},
                "selfVerify": {"enabled": True},
                "worktree": {"enabled": True},
                "taskBoard": {"enabled": True},
                "background": {"autoResume": True},
                "stall": {"autoCancel": True},
                "agentMessages": {"parentTool": True, "continueOnUnread": False},
            },
        },
    )
    _write(orin_managed_dir() / "managed-settings.json", {})

    settings = load_effective_settings(project, trusted=True)

    # Quota and spend keys from repository are discarded
    assert resolve(settings, "quota.policy.enabled").value is False
    assert resolve(settings, "quota.policy.warnPercent").value == 80
    assert resolve(settings, "quota.fallback.enabled").value is False
    assert resolve(settings, "effortByModel").value == {}
    assert resolve(settings, "claudeCli.longContextWithCredits").value is False
    assert settings.get("friend") is None
    assert settings.get("goal") is None
    assert settings.get("pipelines.agentMayStart") is None
    assert settings.get("pipelines.enabled") is None  # True ignored from project

    # Effort dict in repository keeps ONLY valid maxForSubagents
    assert resolve(settings, "effort.boostKeyword").value is False
    assert resolve(settings, "effort.planMode").value is None
    assert resolve(settings, "effort.subagentPolicy").value == "inherit"
    assert resolve(settings, "effort.escalateOnFailure").value is False
    assert resolve(settings, "effort.maxForSubagents") == ResolvedSetting("medium", "project", None)
    assert settings.get("effort.unknown") is None

    # PROJECT_TRUE_IGNORED: False kept, True dropped
    assert settings.get("orchestration.verifier.enabled") is False
    assert settings.get("orchestration.selfVerify.enabled") is None
    assert settings.get("orchestration.worktree.enabled") is None
    assert settings.get("orchestration.agentMessages.parentTool") is None

    # USER_ONLY orchestration subkeys dropped from project
    assert settings.get("orchestration.verifier.modelPolicy") is None
    assert settings.get("orchestration.verifier.explicit") is None
    assert settings.get("orchestration.verifier.effort") is None
    assert settings.get("orchestration.taskBoard") is None
    assert settings.get("orchestration.background") is None
    assert settings.get("orchestration.stall.autoCancel") is None
    assert settings.get("orchestration.agentMessages.continueOnUnread") is None


def test_loader_still_drops_scalar_effort_and_empty_effort_dict_from_project(tmp_path: Path) -> None:
    repo_scalar = tmp_path / "repo_scalar"
    _write(repo_scalar / ".orin" / "settings.json", {"effort": "ultra"})
    settings_scalar = load_effective_settings(repo_scalar, trusted=True)
    assert settings_scalar.get("effort") is None

    repo_dict = tmp_path / "repo_dict"
    _write(repo_dict / ".orin" / "settings.json", {"effort": {"boostKeyword": True, "maxForSubagents": "bogus"}})
    settings_dict = load_effective_settings(repo_dict, trusted=True)
    assert settings_dict.get("effort") is None
