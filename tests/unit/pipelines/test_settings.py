"""WS-C10: the five Package C keys as the pipeline code reads them, and the project budget ceiling (G6)."""

from __future__ import annotations

import dataclasses

import pytest

from agentos.agentic.usage_budget import BudgetLimits
from agentos.pipelines import schema_v1 as sv
from agentos.pipelines.model import Budget
from agentos.pipelines.settings import PipelineSettings, cap_budget_to_ceiling, ceiling_from_settings
from agentos.pipelines.validator import validate_structure
from agentos.settings.loader import EffectiveSettings, SettingsLayer, empty_settings, load_effective_settings
from agentos.settings.registry import validate_change

from .helpers import pipeline


def _settings(*layers: tuple[str, dict]) -> EffectiveSettings:
    built = tuple(SettingsLayer(name, None, data) for name, data in layers)
    return EffectiveSettings(data={}, provenance={}, layers=built, errors=(), trusted=True)


def test_defaults_are_the_ones_of_the_spec() -> None:
    settings = PipelineSettings.from_effective(empty_settings())
    assert settings.enabled is True
    assert settings.agent_may_start is False
    assert settings.subagent_max_tokens is None
    assert settings.subagent_max_cost_usd is None
    assert settings.goal_default_max_tokens == 300000


def test_a_missing_settings_object_means_the_defaults() -> None:
    assert PipelineSettings.from_effective(None) == PipelineSettings.from_effective(empty_settings())


def test_user_layer_values_are_read() -> None:
    effective = _settings(
        (
            "user",
            {
                "pipelines": {"enabled": True, "agentMayStart": True},
                "orchestration": {"budget": {"subagent": {"maxTokens": 90000, "maxCostUsd": 2.5}}},
                "goal": {"defaultMaxTokens": 120000},
            },
        )
    )
    settings = PipelineSettings.from_effective(effective)
    assert settings.agent_may_start is True
    assert settings.subagent_max_tokens == 90000
    assert settings.subagent_max_cost_usd == 2.5
    assert settings.goal_default_max_tokens == 120000


def test_agent_may_start_and_goal_from_the_project_layer_are_ignored() -> None:
    effective = _settings(
        ("project", {"pipelines": {"agentMayStart": True}, "goal": {"defaultMaxTokens": 5000000}}),
    )
    settings = PipelineSettings.from_effective(effective)
    assert settings.agent_may_start is False
    assert settings.goal_default_max_tokens == 300000


def test_the_project_can_only_switch_pipelines_off() -> None:
    assert PipelineSettings.from_effective(_settings(("project", {"pipelines": {"enabled": False}}))).enabled is False
    on_in_project = _settings(("user", {"pipelines": {"enabled": False}}), ("project", {"pipelines": {"enabled": True}}))
    assert PipelineSettings.from_effective(on_in_project).enabled is False


def test_the_project_budget_ceiling_only_lowers_the_user_one() -> None:
    both = _settings(
        ("user", {"orchestration": {"budget": {"subagent": {"maxTokens": 100000}}}}),
        ("project", {"orchestration": {"budget": {"subagent": {"maxTokens": 400000}}}}),
    )
    assert PipelineSettings.from_effective(both).subagent_max_tokens == 100000
    lower = _settings(
        ("user", {"orchestration": {"budget": {"subagent": {"maxTokens": 100000}}}}),
        ("project", {"orchestration": {"budget": {"subagent": {"maxTokens": 40000}}}}),
    )
    assert PipelineSettings.from_effective(lower).subagent_max_tokens == 40000


def test_the_project_layer_of_the_loader_drops_what_a_repository_may_not_set(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "repo"
    (root / ".orin").mkdir(parents=True)
    (root / ".orin" / "settings.json").write_text(
        '{"pipelines": {"agentMayStart": true, "enabled": true}, "goal": {"defaultMaxTokens": 1}}', encoding="utf-8"
    )
    effective = load_effective_settings(root, sources=("project",))
    settings = PipelineSettings.from_effective(effective)
    assert settings.agent_may_start is False
    assert settings.goal_default_max_tokens == 300000


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("pipelines.enabled", False),
        ("pipelines.agentMayStart", True),
        ("orchestration.budget.subagent.maxTokens", 1000),
        ("orchestration.budget.subagent.maxCostUsd", 0.5),
        ("goal.defaultMaxTokens", 250000),
    ],
)
def test_the_registry_validates_the_five_keys_for_the_put_route(path: str, value: object) -> None:
    assert validate_change(path, value) == value


# --------------------------------------------------------------------------------------- the ceiling (G6)
def test_ceiling_from_settings_reads_the_subagent_budget() -> None:
    effective = _settings(("user", {"orchestration": {"budget": {"subagent": {"maxTokens": 80000, "maxCostUsd": 3}}}}))
    ceiling = ceiling_from_settings(effective)
    assert ceiling == BudgetLimits(max_tokens=80000, max_cost_usd=3)
    assert ceiling_from_settings(empty_settings()) == BudgetLimits()


def _definition(budget_line: str, step_budget: str = ""):  # noqa: ANN202
    header = f"version: 1\nid: teste\nname: Teste\n{budget_line}"
    step = (
        "steps:\n  - id: um\n    agent: general\n    prompt: faça\n" + (f"    budget: {step_budget}\n" if step_budget else "")
    )
    definition, report = validate_structure(header + step, "project")
    assert definition is not None, report.errors
    return definition


def test_a_project_budget_above_the_ceiling_is_reduced_to_it() -> None:
    definition = _definition("budget: {max_tokens: 900000, max_cost_usd: 20, max_minutes: 30}\n", "{max_tokens: 500000}")
    capped = cap_budget_to_ceiling(definition, BudgetLimits(max_tokens=100000, max_cost_usd=5))
    assert capped.budget == Budget(max_tokens=100000, max_cost_usd=5, max_minutes=30)
    assert capped.steps[0].budget.max_tokens == 100000
    assert definition.budget.max_tokens == 900000  # the original is untouched


def test_a_project_budget_below_the_ceiling_is_kept() -> None:
    definition = _definition("budget: {max_tokens: 50000}\n")
    capped = cap_budget_to_ceiling(definition, BudgetLimits(max_tokens=100000))
    assert capped.budget == Budget(max_tokens=50000)


def test_a_project_without_budget_gets_the_default_ceiling_clamped() -> None:
    definition = _definition("")
    capped = cap_budget_to_ceiling(definition, BudgetLimits(max_tokens=100000, max_cost_usd=1))
    assert capped.budget == Budget(
        max_tokens=100000, max_cost_usd=1, max_minutes=sv.DEFAULT_BUDGET_MINUTES
    )


def test_without_a_ceiling_nothing_changes() -> None:
    definition = _definition("budget: {max_tokens: 900000}\n")
    assert cap_budget_to_ceiling(definition, BudgetLimits()) is definition
    assert dataclasses.is_dataclass(definition)


def test_only_project_pipelines_are_capped() -> None:
    text = pipeline("- id: um\n  agent: general\n  prompt: faça")
    user, _ = validate_structure(text, "user")
    assert user is not None
    assert cap_budget_to_ceiling(user, BudgetLimits(max_tokens=1)) is user
