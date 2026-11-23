"""Hierarchical usage budgets (tokens, cost, time) for subagents and, later, pipeline runs.

A :class:`UsageBudget` counts what one agent spends and passes every amount up to its parent, so a
parent's (or a pipeline's) limit also covers its children and grandchildren. It never imports the
session: the session feeds it from ``record_usage`` and cancels the agent when it trips.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

BUDGET_KEYS: tuple[str, ...] = ("max_tokens", "max_seconds", "max_cost_usd")
EXCEEDED_ERROR_CODE = "SUBAGENT_BUDGET_EXCEEDED"
_MAX_PARENT_WALK = 16


def _positive(name: str, value: object, *, integer: bool) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be a finite positive number.")
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    if integer:
        if int(value) != value:
            raise ValueError(f"{name} must be a whole number.")
        return int(value)
    return float(value)


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """Ceilings for one agent. ``None`` means no ceiling for that dimension."""

    max_tokens: int | None = None
    max_seconds: float | None = None
    max_cost_usd: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_tokens", _positive("max_tokens", self.max_tokens, integer=True))
        object.__setattr__(self, "max_seconds", _positive("max_seconds", self.max_seconds, integer=False))
        object.__setattr__(self, "max_cost_usd", _positive("max_cost_usd", self.max_cost_usd, integer=False))

    @property
    def is_empty(self) -> bool:
        return self.max_tokens is None and self.max_seconds is None and self.max_cost_usd is None

    def as_dict(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in BUDGET_KEYS if getattr(self, key) is not None}


def parse_budget(raw: object) -> BudgetLimits:
    """Validate the public ``budget`` argument of ``Agent``; raises ``ValueError`` with a user-facing reason."""
    if raw is None:
        return BudgetLimits()
    if isinstance(raw, BudgetLimits):
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError("budget must be an object with max_tokens, max_seconds and/or max_cost_usd.")
    unknown = sorted(str(key) for key in raw if key not in BUDGET_KEYS)
    if unknown:
        raise ValueError(f"budget has unknown keys: {', '.join(unknown)}. Allowed: {', '.join(BUDGET_KEYS)}.")
    return BudgetLimits(**{key: raw.get(key) for key in BUDGET_KEYS})


def effective_budget(call: BudgetLimits, ceiling: BudgetLimits) -> BudgetLimits:
    """The call's budget limited by the user's default ceiling: the smaller value wins in each dimension."""

    def lowest(a: float | int | None, b: float | int | None) -> float | int | None:
        return b if a is None else a if b is None else min(a, b)

    return BudgetLimits(
        max_tokens=lowest(call.max_tokens, ceiling.max_tokens),
        max_seconds=lowest(call.max_seconds, ceiling.max_seconds),
        max_cost_usd=lowest(call.max_cost_usd, ceiling.max_cost_usd),
    )


def default_subagent_budget(effective_settings: object | None) -> BudgetLimits:
    """``orchestration.budget.subagent.*`` as resolved from the layers (a repository only lowers them)."""
    if effective_settings is None:
        return BudgetLimits()
    from agentos.settings.registry import resolve

    def read(path: str) -> Any:
        try:
            return resolve(effective_settings, path).value  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - an unreadable setting means no ceiling, never a failed delegation
            return None

    tokens = read("orchestration.budget.subagent.maxTokens")
    if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) and math.isfinite(tokens):
        tokens = max(1, int(tokens))  # the registry's "number" accepts decimals; a ceiling rounds down, never vanishes
    try:
        return BudgetLimits(max_tokens=tokens, max_cost_usd=read("orchestration.budget.subagent.maxCostUsd"))
    except ValueError:
        return BudgetLimits()


@dataclass(frozen=True, slots=True)
class BudgetExceeded:
    """Which budget went over, on what dimension, and by how much."""

    agent_id: str
    kind: str  # "tokens" | "cost" | "time"
    limit: float
    used: float

    @property
    def message(self) -> str:
        unit = {"tokens": "tokens", "cost": "USD", "time": "seconds"}.get(self.kind, self.kind)
        return f"{self.kind} budget exceeded: used {self.used:g} of {self.limit:g} {unit}"


class UsageBudget:
    """Counts one agent's tokens, known cost and elapsed time against :class:`BudgetLimits`.

    ``add`` also adds to every ancestor, so a child's spending counts against its parents. A budget that goes
    over trips once: it sets its ``cancel`` event (when given) and is returned by the ``add``/``exceeded`` call
    that noticed it, so the caller can record the event. Cost is only counted when known; a subscription CLI
    reports none, so then only tokens and time can trip (``cost_known`` is False).
    """

    def __init__(
        self,
        limits: BudgetLimits,
        *,
        agent_id: str = "",
        parent: UsageBudget | None = None,
        cancel: threading.Event | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits
        self.agent_id = agent_id
        self.parent = parent
        self.cancel = cancel
        self._clock = clock
        self._started = clock()
        self._lock = threading.Lock()
        self.tokens = 0
        self.cost_usd = 0.0
        self.cost_known = True
        self._tripped: BudgetExceeded | None = None

    @property
    def elapsed_seconds(self) -> float:
        return self._clock() - self._started

    def add(self, tokens: int, cost: float | None) -> list[BudgetExceeded]:
        """Count one usage record here and in every ancestor; return the budgets that tripped because of it."""
        tripped: list[BudgetExceeded] = []
        node: UsageBudget | None = self
        over = False
        while node is not None:
            with node._lock:
                node.tokens += max(0, int(tokens or 0))
                if cost is None:
                    node.cost_known = False
                else:
                    node.cost_usd += float(cost)
            fresh = node._trip_if_over()
            if fresh is not None:
                tripped.append(fresh)
            over = over or node._tripped is not None
            node = node.parent
        if over and self.cancel is not None:
            self.cancel.set()  # whoever spends under a budget that is over stops, even when the ancestor tripped earlier
        return tripped

    @property
    def tripped(self) -> BudgetExceeded | None:
        """Why this budget already tripped (no clock check), or None."""
        return self._tripped

    @property
    def over(self) -> BudgetExceeded | None:
        """Why this budget or the nearest ancestor that already tripped is over, or None."""
        node: UsageBudget | None = self
        while node is not None:
            if node._tripped is not None:
                return node._tripped
            node = node.parent
        return None

    def exceeded(self) -> BudgetExceeded | None:
        """The reason this budget is over (checking the clock too), or None. Trips it the first time."""
        self._trip_if_over()
        return self._tripped

    def trip_if_over(self) -> BudgetExceeded | None:
        """Like :meth:`exceeded`, but returns the reason only on the call that tripped it (for a timer)."""
        return self._trip_if_over()

    def _trip_if_over(self) -> BudgetExceeded | None:
        with self._lock:
            if self._tripped is not None:
                return None
            reason = self._reason_locked()
            if reason is None:
                return None
            self._tripped = reason
        if self.cancel is not None:
            self.cancel.set()
        return reason

    def _reason_locked(self) -> BudgetExceeded | None:
        limits = self.limits
        if limits.max_tokens is not None and self.tokens > limits.max_tokens:
            return BudgetExceeded(self.agent_id, "tokens", limits.max_tokens, self.tokens)
        if limits.max_cost_usd is not None and self.cost_usd > limits.max_cost_usd:
            return BudgetExceeded(self.agent_id, "cost", limits.max_cost_usd, round(self.cost_usd, 6))
        if limits.max_seconds is not None and self.elapsed_seconds > limits.max_seconds:
            return BudgetExceeded(self.agent_id, "time", limits.max_seconds, round(self.elapsed_seconds, 3))
        return None


class BudgetRegistry:
    """``agent_id -> UsageBudget`` of one session: where ``record_usage`` finds the budget to feed."""

    def __init__(self) -> None:
        self._budgets: dict[str, UsageBudget] = {}
        self._lock = threading.Lock()

    def get(self, agent_id: str) -> UsageBudget | None:
        with self._lock:
            return self._budgets.get(agent_id)

    def register(self, agent_id: str, budget: UsageBudget) -> UsageBudget:
        """Install a budget under ``agent_id`` (a pipeline run registers its own under the main agent's id)."""
        with self._lock:
            self._budgets[agent_id] = budget
        return budget

    def close(self, agent_id: str, budget: UsageBudget | None) -> None:
        with self._lock:
            if budget is not None and self._budgets.get(agent_id) is budget:
                self._budgets.pop(agent_id, None)

    def nearest(self, agent_id: str, parent_of: Callable[[str], str]) -> UsageBudget | None:
        """The budget of ``agent_id`` or of its closest ancestor that has one."""
        seen: set[str] = set()
        current = agent_id
        for _ in range(_MAX_PARENT_WALK):
            if not current or current in seen:
                return None
            seen.add(current)
            found = self.get(current)
            if found is not None:
                return found
            current = parent_of(current)
        return None

    def open(
        self,
        agent_id: str,
        limits: BudgetLimits,
        *,
        caller_id: str,
        parent_of: Callable[[str], str],
        cancel: threading.Event | None,
    ) -> UsageBudget | None:
        """Budget for a starting subagent, parented to the nearest budgeted ancestor.

        Returns None (no overhead) when the agent has no limit and no ancestor is budgeted.
        """
        parent = self.nearest(caller_id, parent_of)
        if limits.is_empty and parent is None:
            return None
        return self.register(agent_id, UsageBudget(limits, agent_id=agent_id, parent=parent, cancel=cancel))

    def add(self, agent_id: str, tokens: int, cost: float | None) -> list[BudgetExceeded]:
        budget = self.get(agent_id)
        return budget.add(tokens, cost) if budget is not None else []


__all__ = [
    "BUDGET_KEYS",
    "EXCEEDED_ERROR_CODE",
    "BudgetExceeded",
    "BudgetLimits",
    "BudgetRegistry",
    "UsageBudget",
    "default_subagent_budget",
    "effective_budget",
    "parse_budget",
]
