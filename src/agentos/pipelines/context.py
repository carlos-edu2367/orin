"""Run context: inputs, step results and loop counters, read and written under one lock (WS-C6)."""

from __future__ import annotations

import copy
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .expressions import resolve_path


@dataclass(slots=True)
class StepResult:
    """What later steps can read of one step: ``output``, ``failed`` and ``findings``."""

    output: Any = None
    failed: bool = False
    findings: list[Any] = field(default_factory=list)
    state: str = "pending"  # succeeded | failed | skipped | gate_failed | interrupted | cancelled

    def as_mapping(self) -> dict[str, Any]:
        return {"output": self.output, "failed": self.failed, "findings": self.findings}


class RunContext:
    """Thread-safe store of what a run knows. ``snapshot`` gives a detached copy for rendering."""

    def __init__(self, inputs: Mapping[str, Any]) -> None:
        self._lock = threading.Lock()
        self._inputs: dict[str, Any] = dict(inputs)
        self._steps: dict[str, StepResult] = {}
        self._visits: dict[str, int] = {}  # times a step was entered so far (its ``loop_count`` of the next visit)
        self._goto_counts: dict[str, int] = {}  # backward jumps taken by ``on_fail.goto`` per source step
        self.caveats: list[str] = []

    # ------------------------------------------------------------------ inputs
    @property
    def inputs(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._inputs)

    def input_value(self, name: str) -> Any:
        with self._lock:
            return self._inputs.get(name)

    # ------------------------------------------------------------------- steps
    def set_result(self, step_id: str, result: StepResult) -> None:
        with self._lock:
            self._steps[step_id] = result

    def result(self, step_id: str) -> StepResult | None:
        with self._lock:
            return self._steps.get(step_id)

    def failed(self, step_id: str) -> bool:
        with self._lock:
            found = self._steps.get(step_id)
            return bool(found and found.failed)

    # ------------------------------------------------------------------ counters
    def enter(self, step_id: str) -> int:
        """Register a visit and return its ``loop_count`` (0 on the first one)."""
        with self._lock:
            count = self._visits.get(step_id, 0)
            self._visits[step_id] = count + 1
            return count

    def visits(self, step_id: str) -> int:
        with self._lock:
            return self._visits.get(step_id, 0)

    def bump_goto(self, step_id: str) -> int:
        with self._lock:
            self._goto_counts[step_id] = self._goto_counts.get(step_id, 0) + 1
            return self._goto_counts[step_id]

    def goto_count(self, step_id: str) -> int:
        with self._lock:
            return self._goto_counts.get(step_id, 0)

    def total_loops(self) -> int:
        with self._lock:
            return sum(self._goto_counts.values())

    def add_caveat(self, text: str) -> None:
        with self._lock:
            if text not in self.caveats:
                self.caveats.append(text)

    # ---------------------------------------------------------------- rendering
    def snapshot(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Detached view ``{"inputs": ..., "steps": {id: {output, failed, findings}}, **extra}`` for expressions."""
        with self._lock:
            view: dict[str, Any] = {
                "inputs": copy.deepcopy(self._inputs),
                "steps": {key: copy.deepcopy(value.as_mapping()) for key, value in self._steps.items()},
            }
        if extra:
            view.update(extra)
        return view


def make_resolver(view: Mapping[str, Any]):  # type: ignore[no-untyped-def]
    """Resolver for ``expressions.evaluate``/``render_template`` over a snapshot (missing path -> ``None``)."""

    def resolve(segments: tuple[str, ...]) -> object:
        return resolve_path(view, segments)

    return resolve


__all__ = ["RunContext", "StepResult", "make_resolver"]
