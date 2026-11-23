"""Pipeline budget over ``UsageBudget`` (C4): limits from the YAML, time on the injected clock (WS-C6).

The run's ``UsageBudget`` is the parent of every child's own budget. With a live-metering launcher the session
feeds it as the children spend; with any other launcher the engine charges it from progress and results. When
it trips it sets ``cancel`` and the engine stops everything in flight (the hard ceiling of decision G5).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from decimal import Decimal

from agentos.agentic.usage_budget import BudgetExceeded, BudgetLimits, UsageBudget

from . import schema_v1 as sv
from .model import Budget
from .ports import Clock

_MIN_TOKENS = 1
_MIN_SECONDS = 1.0
_MIN_COST = 0.000001


def pipeline_limits(budget: Budget | None) -> tuple[BudgetLimits, bool]:
    """Limits of the run. A pipeline without ``budget`` gets the default ceiling (G8); the flag says it did."""
    if budget is None or budget.is_empty:
        return (
            BudgetLimits(
                max_tokens=sv.DEFAULT_BUDGET_TOKENS,
                max_cost_usd=sv.DEFAULT_BUDGET_COST_USD,
                max_seconds=sv.DEFAULT_BUDGET_MINUTES * 60.0,
            ),
            True,
        )
    return (
        BudgetLimits(
            max_tokens=budget.max_tokens,
            max_cost_usd=budget.max_cost_usd,
            max_seconds=budget.max_minutes * 60.0 if budget.max_minutes is not None else None,
        ),
        False,
    )


class PipelineBudget:
    def __init__(
        self,
        budget: Budget | None,
        clock: Clock,
        run_id: str,
        *,
        used_tokens: int = 0,
        used_cost_usd: Decimal | float | None = None,
        used_seconds: float = 0.0,
        cost_known: bool = True,
    ) -> None:
        self.limits, self.is_default = pipeline_limits(budget)
        self.cancel = threading.Event()
        offset = float(used_seconds)
        monotonic: Callable[[], float] = clock.monotonic
        self.usage = UsageBudget(
            self.limits, agent_id=run_id, cancel=self.cancel, clock=lambda: monotonic() + offset
        )
        self.usage.tokens = int(used_tokens)
        self.usage.cost_known = bool(cost_known)  # a resumed run keeps what its earlier steps already made unknown
        if used_cost_usd is not None:
            self.usage.cost_usd = float(used_cost_usd)
        self._clock = clock
        self._announced = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ state
    def exceeded(self) -> BudgetExceeded | None:
        """Why the pipeline is over its budget (clock included), or ``None``."""
        return self.usage.exceeded()

    @property
    def tripped(self) -> BudgetExceeded | None:
        return self.usage.tripped

    def claim_announcement(self) -> bool:
        """``True`` exactly once, for the thread that should emit ``pipeline.budget_exceeded``."""
        with self._lock:
            if self._announced:
                return False
            self._announced = True
            return True

    def charge(self, tokens: int, cost: float | None) -> list[BudgetExceeded]:
        return self.usage.add(tokens, cost)

    @property
    def cost_known(self) -> bool:
        return self.usage.cost_known

    @property
    def elapsed_seconds(self) -> float:
        return self.usage.elapsed_seconds

    # ------------------------------------------------------------- child caps
    def agent_budget(self, step_budget: Budget | None) -> dict[str, float | int] | None:
        """``AgentCall.budget`` for one launched agent: its own ceiling (G5), never above what the pipeline has left."""
        limits = self.limits
        wanted: dict[str, float | int] = {}
        if step_budget is not None:
            if step_budget.max_tokens is not None:
                wanted["max_tokens"] = step_budget.max_tokens
            if step_budget.max_cost_usd is not None:
                wanted["max_cost_usd"] = step_budget.max_cost_usd
            if step_budget.max_minutes is not None:
                wanted["max_seconds"] = step_budget.max_minutes * 60.0  # G26: minutes in the YAML, seconds in Agent
        if limits.max_tokens is not None:
            left = max(_MIN_TOKENS, limits.max_tokens - self.usage.tokens)
            wanted["max_tokens"] = min(wanted.get("max_tokens", left), left)
        if limits.max_cost_usd is not None:
            left_cost = max(_MIN_COST, limits.max_cost_usd - self.usage.cost_usd)
            wanted["max_cost_usd"] = min(wanted.get("max_cost_usd", left_cost), left_cost)
        if limits.max_seconds is not None:
            left_time = max(_MIN_SECONDS, limits.max_seconds - self.usage.elapsed_seconds)
            wanted["max_seconds"] = min(wanted.get("max_seconds", left_time), left_time)
        return wanted or None


__all__ = ["PipelineBudget", "pipeline_limits"]
