"""Cost and token telemetry over the pipeline journal (WS-C5, spec 7.7 "tokens e custo por etapa").

Reads ``PipelineStore`` only. The numbers are the journal's: every attempt that ran counts once, a step that a
resume reused writes no row (the engine only emits ``pipeline.step_reused``) and therefore adds nothing twice.

Cost rules (plan P5): an unknown cost is ``None`` with ``cost_known=False``, never zero. When some rows have a
cost and others do not, ``cost_usd`` is the sum of the known ones (a lower bound) and ``cost_known`` is false.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .store import PipelineStore, StepRecord

_COST_TOLERANCE = Decimal("0.000001")


def _as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class UsageRow:
    key: str
    attempts: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    cost_known: bool
    latest_states: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "attempts": self.attempts,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "cost_known": self.cost_known,
            "latest_states": dict(self.latest_states),
        }


@dataclass(frozen=True, slots=True)
class RunUsed:
    """What the run row itself counted (``used_*`` columns), to compare with the journal."""

    tokens: int
    cost_usd: float | None
    seconds: int


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    pipeline_id: str
    state: str
    stop_reason: str | None
    total: UsageRow
    by_step: tuple[UsageRow, ...]
    by_agent: tuple[UsageRow, ...]
    by_provider: tuple[UsageRow, ...]
    run_used: RunUsed
    consistent: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "state": self.state,
            "stop_reason": self.stop_reason,
            "total": self.total.to_dict(),
            "by_step": [row.to_dict() for row in self.by_step],
            "by_agent": [row.to_dict() for row in self.by_agent],
            "by_provider": [row.to_dict() for row in self.by_provider],
            "run_used": {
                "tokens": self.run_used.tokens,
                "cost_usd": self.run_used.cost_usd,
                "seconds": self.run_used.seconds,
            },
            "consistent": self.consistent,
        }


def _latest_states(steps: list[StepRecord]) -> dict[str, dict[str, int]]:
    """For each step id, count the states of the latest attempt of every ``(item_key, loop_count)``."""
    latest: dict[tuple[str, str, int], StepRecord] = {}
    for step in steps:
        key = (step.step_id, step.item_key, step.loop_count)
        if key not in latest or step.attempt > latest[key].attempt:
            latest[key] = step
    counters: dict[str, Counter[str]] = {}
    for (step_id, _item, _loop), step in latest.items():
        counters.setdefault(step_id, Counter())[step.state] += 1
    return {step_id: dict(counter) for step_id, counter in counters.items()}


def _rows(store: PipelineStore, run_id: str, group: str, states: dict[str, dict[str, int]]) -> tuple[UsageRow, ...]:
    return tuple(
        UsageRow(
            key=row["key"],
            attempts=row["rows"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_usd=_as_float(row["cost_usd"]),
            cost_known=bool(row["cost_known"]),
            latest_states=states.get(row["key"], {}) if group == "step" else {},
        )
        for row in store.usage_by(run_id, group)
    )


def _total(rows: tuple[UsageRow, ...], attempts: int) -> UsageRow:
    known = [row.cost_usd for row in rows if row.cost_usd is not None]
    all_known = all(row.cost_known for row in rows)
    if known:
        cost: float | None = sum(known)
    else:
        cost = 0.0 if all_known else None
    return UsageRow(
        key="total",
        attempts=attempts,
        input_tokens=sum(row.input_tokens for row in rows),
        output_tokens=sum(row.output_tokens for row in rows),
        cost_usd=cost,
        cost_known=all_known,
    )


def run_summary(store: PipelineStore, run_id: str) -> RunSummary:
    """Totals of a run by step, agent and provider, plus a check against the run's own counters."""
    run = store.get_run(run_id)
    if run is None:
        raise LookupError(f"execucao de pipeline desconhecida: {run_id}")
    steps = store.steps_of(run_id)
    states = _latest_states(steps)
    by_step = _rows(store, run_id, "step", states)
    by_agent = _rows(store, run_id, "agent", states)
    by_provider = _rows(store, run_id, "provider", states)
    total = _total(by_step, attempts=len(steps))
    run_used = RunUsed(tokens=run.used_tokens, cost_usd=_as_float(run.used_cost_usd), seconds=run.used_seconds)
    consistent = run.used_tokens == total.total_tokens
    if consistent and total.cost_known and total.cost_usd is not None:
        counted = run.used_cost_usd if run.used_cost_usd is not None else Decimal("0")
        consistent = abs(counted - Decimal(str(total.cost_usd))) <= _COST_TOLERANCE
    return RunSummary(
        run_id=run.run_id,
        pipeline_id=run.pipeline_id,
        state=run.state,
        stop_reason=run.stop_reason,
        total=total,
        by_step=by_step,
        by_agent=by_agent,
        by_provider=by_provider,
        run_used=run_used,
        consistent=consistent,
    )


__all__ = ["RunSummary", "RunUsed", "UsageRow", "run_summary"]
