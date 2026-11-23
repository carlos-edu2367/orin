"""WS-C4: the five Package C setting keys live in the E0 registry with the spec's names, defaults and scopes."""

from __future__ import annotations

import pytest

from agentos.settings.loader import EffectiveSettings, SettingsLayer, empty_settings
from agentos.settings.registry import REGISTRY, ResolvedSetting, Scope, resolve, validate_change

EXPECTED = {
    "pipelines.enabled": (True, Scope.PROJECT_OFF_ONLY, "boolean"),
    "pipelines.agentMayStart": (False, Scope.USER_ONLY, "boolean"),
    "orchestration.budget.subagent.maxTokens": (None, Scope.PROJECT_LOWER_ONLY, "number"),
    "orchestration.budget.subagent.maxCostUsd": (None, Scope.PROJECT_LOWER_ONLY, "number"),
    "goal.defaultMaxTokens": (300000, Scope.USER_ONLY, "number"),
}


def _synthetic(*layers: tuple[str, dict]) -> EffectiveSettings:
    built = tuple(SettingsLayer(name, None, data) for name, data in layers)
    return EffectiveSettings(data={}, provenance={}, layers=built, errors=(), trusted=True)


@pytest.mark.parametrize("path", sorted(EXPECTED))
def test_package_c_keys_registered_with_spec_defaults_and_scopes(path: str) -> None:
    default, scope, key_type = EXPECTED[path]
    assert path in REGISTRY
    key = REGISTRY[path]
    assert (key.default, key.scope, key.type) == (default, scope, key_type)
    assert key.label.strip() and key.description.strip()
    assert resolve(empty_settings(), path) == ResolvedSetting(value=default, layer="default", default=default)


@pytest.mark.parametrize(
    ("path", "bad"),
    [
        ("pipelines.enabled", "yes"),
        ("pipelines.agentMayStart", 1),
        ("orchestration.budget.subagent.maxTokens", "100"),
        ("orchestration.budget.subagent.maxTokens", 0),
        ("orchestration.budget.subagent.maxTokens", -5),
        ("orchestration.budget.subagent.maxCostUsd", 0),
        ("orchestration.budget.subagent.maxCostUsd", -0.01),
        ("orchestration.budget.subagent.maxCostUsd", True),
        ("goal.defaultMaxTokens", 0),
        ("goal.defaultMaxTokens", -1),
        ("goal.defaultMaxTokens", 1.5),
        ("goal.defaultMaxTokens", "300000"),
    ],
)
def test_validate_change_rejects_wrong_type_and_non_positive_values(path: str, bad: object) -> None:
    with pytest.raises(ValueError):
        validate_change(path, bad)


def test_validate_change_accepts_positive_values_and_null_clears_budgets() -> None:
    assert validate_change("orchestration.budget.subagent.maxTokens", 50000) == 50000
    assert validate_change("orchestration.budget.subagent.maxCostUsd", 1.5) == 1.5
    assert validate_change("orchestration.budget.subagent.maxTokens", None) is None
    assert validate_change("orchestration.budget.subagent.maxCostUsd", None) is None
    assert validate_change("goal.defaultMaxTokens", 120000) == 120000
    assert validate_change("pipelines.agentMayStart", True) is True


def test_goal_default_max_tokens_does_not_accept_null() -> None:
    with pytest.raises(ValueError):
        validate_change("goal.defaultMaxTokens", None)


def test_project_layer_only_lowers_the_subagent_budget_and_only_turns_pipelines_off() -> None:
    cap = "orchestration.budget.subagent.maxTokens"
    settings = _synthetic(("user", {"orchestration": {"budget": {"subagent": {"maxTokens": 100000}}}}),
                          ("project", {"orchestration": {"budget": {"subagent": {"maxTokens": 500000}}}}))
    assert resolve(settings, cap).value == 100000
    lowered = _synthetic(("user", {"orchestration": {"budget": {"subagent": {"maxTokens": 100000}}}}),
                         ("project", {"orchestration": {"budget": {"subagent": {"maxTokens": 40000}}}}))
    assert resolve(lowered, cap).value == 40000
    assert resolve(lowered, cap).layer == "project"
    # A repository cannot turn pipelines on or let the agent start them.
    assert resolve(_synthetic(("project", {"pipelines": {"enabled": True}})), "pipelines.enabled").layer == "default"
    assert resolve(_synthetic(("project", {"pipelines": {"enabled": False}})), "pipelines.enabled").value is False
    assert resolve(_synthetic(("project", {"pipelines": {"agentMayStart": True}})), "pipelines.agentMayStart").value is False
