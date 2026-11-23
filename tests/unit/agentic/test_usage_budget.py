"""WS-C4: hierarchical usage budget (tokens, cost, time) with no dependency on the session."""

from __future__ import annotations

import threading

import pytest

from agentos.agentic.usage_budget import (
    BudgetLimits,
    UsageBudget,
    default_subagent_budget,
    effective_budget,
    parse_budget,
)
from agentos.settings.loader import EffectiveSettings, SettingsLayer


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_tokens_limit_trips_and_sets_the_cancel_event() -> None:
    cancel = threading.Event()
    budget = UsageBudget(BudgetLimits(max_tokens=1000), agent_id="a", cancel=cancel)
    assert budget.add(600, None) == []
    assert budget.exceeded() is None and not cancel.is_set()
    tripped = budget.add(600, None)
    assert [item.agent_id for item in tripped] == ["a"]
    assert tripped[0].kind == "tokens" and tripped[0].limit == 1000 and tripped[0].used == 1200
    assert budget.exceeded() is not None and cancel.is_set()
    assert budget.add(10, None) == []  # trips once


def test_cost_limit_counts_only_known_costs_and_unknown_cost_never_trips() -> None:
    budget = UsageBudget(BudgetLimits(max_cost_usd=1.0), agent_id="a")
    assert budget.add(10_000_000, None) == []  # subscription CLI: no price, tokens only
    assert budget.cost_known is False
    assert budget.exceeded() is None
    assert budget.add(10, 0.6) == []
    assert [i.kind for i in budget.add(10, 0.6)] == ["cost"]
    assert budget.cost_usd == pytest.approx(1.2)


def test_time_limit_uses_the_injected_clock() -> None:
    clock = _Clock()
    budget = UsageBudget(BudgetLimits(max_seconds=30), agent_id="a", clock=clock)
    assert budget.exceeded() is None
    clock.now += 29
    assert budget.exceeded() is None
    clock.now += 2
    tripped = budget.exceeded()
    assert tripped is not None and tripped.kind == "time"
    assert budget.elapsed_seconds == pytest.approx(31)


def test_child_usage_flows_to_parent_and_grandparent_and_each_trips_on_its_own_limit() -> None:
    root = UsageBudget(BudgetLimits(max_tokens=1500), agent_id="pipeline", cancel=threading.Event())
    parent = UsageBudget(BudgetLimits(max_tokens=1000), agent_id="parent", parent=root, cancel=threading.Event())
    child = UsageBudget(BudgetLimits(), agent_id="child", parent=parent, cancel=threading.Event())
    assert child.add(900, None) == []
    assert (child.tokens, parent.tokens, root.tokens) == (900, 900, 900)
    tripped = child.add(200, None)
    assert [item.agent_id for item in tripped] == ["parent"]  # the child has no limit; the parent just went over
    assert parent.cancel.is_set() and not root.cancel.is_set()
    assert child.cancel.is_set()  # the spender stops too: it works under a parent that is over
    tripped = child.add(600, None)
    assert [item.agent_id for item in tripped] == ["pipeline"]
    assert root.cancel.is_set()


def test_a_sibling_spending_after_the_ancestor_tripped_is_stopped_too() -> None:
    root = UsageBudget(BudgetLimits(max_tokens=100), agent_id="pipeline", cancel=threading.Event())
    first = UsageBudget(BudgetLimits(), agent_id="first", parent=root, cancel=threading.Event())
    second = UsageBudget(BudgetLimits(), agent_id="second", parent=root, cancel=threading.Event())
    assert [i.agent_id for i in first.add(150, None)] == ["pipeline"]
    assert first.cancel.is_set() and not second.cancel.is_set()
    assert second.add(1, None) == []  # the root already tripped: nothing new to announce...
    assert second.cancel.is_set()  # ...but the sibling stops as soon as it spends
    assert second.over is not None and second.over.agent_id == "pipeline" and second.tripped is None


def test_unlimited_budget_only_counts() -> None:
    budget = UsageBudget(BudgetLimits(), agent_id="a")
    assert budget.add(5, 0.1) == []
    assert (budget.tokens, budget.cost_usd) == (5, pytest.approx(0.1))
    assert budget.exceeded() is None


def test_sixteen_threads_sum_exactly_into_child_and_parent() -> None:
    parent = UsageBudget(BudgetLimits(), agent_id="p")
    children = [UsageBudget(BudgetLimits(), agent_id=f"c{i}", parent=parent) for i in range(16)]
    barrier = threading.Barrier(16, timeout=10)

    def work(child: UsageBudget) -> None:
        barrier.wait()
        for _ in range(10_000):
            child.add(1, 0.0001)

    threads = [threading.Thread(target=work, args=(child,)) for child in children]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(child.tokens == 10_000 for child in children)
    assert parent.tokens == 160_000
    assert parent.cost_usd == pytest.approx(16.0, rel=1e-6)


@pytest.mark.parametrize("bad", [0, -1, -0.5])
@pytest.mark.parametrize("field", ["max_tokens", "max_seconds", "max_cost_usd"])
def test_non_positive_limit_is_a_validation_error(field: str, bad: float) -> None:
    with pytest.raises(ValueError):
        BudgetLimits(**{field: bad})


def test_parse_budget_accepts_the_three_keys_and_normalises_types() -> None:
    limits = parse_budget({"max_tokens": 1000, "max_seconds": 90.5, "max_cost_usd": 2})
    assert limits == BudgetLimits(max_tokens=1000, max_seconds=90.5, max_cost_usd=2.0)
    assert parse_budget(None) == BudgetLimits() and parse_budget({}) == BudgetLimits()


@pytest.mark.parametrize(
    "raw",
    [
        {"max_token": 10},
        {"max_tokens": "10"},
        {"max_tokens": True},
        {"max_tokens": 1.5},
        {"max_tokens": -3},
        {"max_seconds": "x"},
        {"max_cost_usd": float("nan")},
        {"max_cost_usd": float("inf")},
        {"max_cost_usd": 0},
        [1000],
        "1000",
    ],
)
def test_parse_budget_rejects_unknown_keys_and_bad_values(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_budget(raw)


def test_effective_budget_user_ceiling_wins_over_the_call() -> None:
    default = BudgetLimits(max_tokens=1000, max_cost_usd=2.0)
    assert effective_budget(BudgetLimits(max_tokens=5000), default) == BudgetLimits(max_tokens=1000, max_cost_usd=2.0)
    assert effective_budget(BudgetLimits(max_tokens=500, max_seconds=60), default) == BudgetLimits(
        max_tokens=500, max_seconds=60, max_cost_usd=2.0
    )
    assert effective_budget(BudgetLimits(), BudgetLimits()) == BudgetLimits()
    assert effective_budget(BudgetLimits(max_tokens=7), BudgetLimits()) == BudgetLimits(max_tokens=7)


def _settings(user: dict | None = None, project: dict | None = None) -> EffectiveSettings:
    layers = []
    if user is not None:
        layers.append(SettingsLayer("user", None, user))
    if project is not None:
        layers.append(SettingsLayer("project", None, project))
    return EffectiveSettings(data={}, provenance={}, layers=tuple(layers), errors=(), trusted=True)


def _budget_data(tokens: object = None, cost: object = None) -> dict:
    sub: dict = {}
    if tokens is not None:
        sub["maxTokens"] = tokens
    if cost is not None:
        sub["maxCostUsd"] = cost
    return {"orchestration": {"budget": {"subagent": sub}}}


def test_default_subagent_budget_reads_settings_and_the_project_only_lowers() -> None:
    assert default_subagent_budget(None) == BudgetLimits()
    assert default_subagent_budget(_settings()) == BudgetLimits()
    assert default_subagent_budget(_settings(user=_budget_data(200000, 3.5))) == BudgetLimits(
        max_tokens=200000, max_cost_usd=3.5
    )
    both = _settings(user=_budget_data(200000), project=_budget_data(900000))
    assert default_subagent_budget(both) == BudgetLimits(max_tokens=200000)
    lowered = _settings(user=_budget_data(200000), project=_budget_data(50000))
    assert default_subagent_budget(lowered) == BudgetLimits(max_tokens=50000)
    assert default_subagent_budget(_settings(project=_budget_data(40000))) == BudgetLimits(max_tokens=40000)


def test_default_budget_rounds_a_decimal_token_ceiling_down_and_never_drops_it() -> None:
    assert default_subagent_budget(_settings(user=_budget_data(1500.9))) == BudgetLimits(max_tokens=1500)
    assert default_subagent_budget(_settings(user=_budget_data(0.4))).max_tokens is None  # below the registry minimum: ignored
