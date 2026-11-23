"""The five Package C settings as the pipeline code reads them, and the budget ceiling of a project pipeline (WS-C10).

The registry of ``settings/keys_pipelines.py`` owns names, types, defaults and the layer rules (a repository can only
switch ``pipelines.enabled`` off, can never set ``pipelines.agentMayStart`` or ``goal.*``, and can only lower the
subagent budget ceilings), so reading goes through ``resolve`` and nothing here re-implements a layer rule.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from typing import Any

from agentos.agentic.usage_budget import BudgetLimits, default_subagent_budget
from agentos.settings.keys_pipelines import DEFAULT_GOAL_MAX_TOKENS

from . import schema_v1 as sv
from .model import Budget, PipelineDefinition

logger = logging.getLogger("agentos.pipelines.settings")


def _read(effective: object | None, path: str, default: Any) -> Any:
    if effective is None:
        return default
    from agentos.settings.registry import resolve

    try:
        value = resolve(effective, path).value  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - an unreadable setting means its default, never a failed pipeline
        logger.debug("could not resolve %s", path, exc_info=True)
        return default
    return default if value is None else value


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 1:
        return None
    return int(value)


def _positive_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


@dataclass(frozen=True, slots=True)
class PipelineSettings:
    enabled: bool = True
    agent_may_start: bool = False
    subagent_max_tokens: int | None = None
    subagent_max_cost_usd: float | None = None
    goal_default_max_tokens: int = DEFAULT_GOAL_MAX_TOKENS

    @classmethod
    def from_effective(cls, effective: object | None) -> PipelineSettings:
        goal = _positive_int(_read(effective, "goal.defaultMaxTokens", DEFAULT_GOAL_MAX_TOKENS))
        return cls(
            enabled=_read(effective, "pipelines.enabled", True) is not False,
            agent_may_start=_read(effective, "pipelines.agentMayStart", False) is True,
            subagent_max_tokens=_positive_int(_read(effective, "orchestration.budget.subagent.maxTokens", None)),
            subagent_max_cost_usd=_positive_float(_read(effective, "orchestration.budget.subagent.maxCostUsd", None)),
            goal_default_max_tokens=goal if goal is not None else DEFAULT_GOAL_MAX_TOKENS,
        )


def ceiling_from_settings(effective: object | None) -> BudgetLimits:
    """The user's ``orchestration.budget.subagent.*`` as one ceiling (a repository only lowered it already)."""
    return default_subagent_budget(effective)


def _lowest(value: float | int | None, ceiling: float | int | None) -> float | int | None:
    if value is None:
        return ceiling
    return value if ceiling is None else min(value, ceiling)


def _capped(budget: Budget | None, ceiling: BudgetLimits) -> Budget | None:
    if budget is None:
        return None
    return Budget(
        max_tokens=_lowest(budget.max_tokens, ceiling.max_tokens),  # type: ignore[arg-type]
        max_cost_usd=_lowest(budget.max_cost_usd, ceiling.max_cost_usd),
        max_minutes=budget.max_minutes,
    )


def cap_budget_to_ceiling(definition: PipelineDefinition, ceiling: BudgetLimits) -> PipelineDefinition:
    """A project pipeline never asks for more than the user's ceiling (decision G6): the budgets of the file only go down.

    The pipeline without ``budget`` runs on the default ceiling (G8), which is lowered the same way. User and
    built-in pipelines are the user's own text and are returned as they are.
    """
    if definition.source != "project" or ceiling.max_tokens is None and ceiling.max_cost_usd is None:
        return definition
    pipeline_budget = definition.budget
    if pipeline_budget is None or pipeline_budget.is_empty:
        pipeline_budget = Budget(sv.DEFAULT_BUDGET_TOKENS, sv.DEFAULT_BUDGET_COST_USD, sv.DEFAULT_BUDGET_MINUTES)
    steps = tuple(
        replace(step, budget=_capped(step.budget, ceiling)) if step.budget is not None else step for step in definition.steps
    )
    return replace(definition, budget=_capped(pipeline_budget, ceiling), steps=steps)


__all__ = ["PipelineSettings", "cap_budget_to_ceiling", "ceiling_from_settings"]
