"""Per-turn efficiency accounting for the agentic loop.

The runtime already knows everything needed to answer "did this turn spend its
tool calls well?" -- it just never wrote it down. This is the smallest object
that records it: how many tools were called, how many of those repeated work
the turn had already done successfully, how many iterations it took and what
the provider charged for.

It exists so a change to the loop can be judged against a measured baseline
instead of an impression. Nothing here influences the turn; a counter that
cannot make sense of an argument still counts the call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Mapping


def signature(name: str, arguments: Mapping[str, object]) -> str:
    """A stable identity for one (tool, arguments) pair.

    Deliberately the same shape ``AgenticTurnRuntime._signature`` uses for its
    failure short-circuit, so "redundant" here means exactly what "duplicate"
    means there. ``sort_keys`` makes argument order irrelevant, which matters
    because a model rarely emits keys in a stable order.
    """
    try:
        return f"{name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)}"
    except (TypeError, ValueError):
        return f"{name}:{arguments!r}"


@dataclass(slots=True)
class TurnQualityCounters:
    """Mutable tally for one turn. Never raises, never blocks the turn."""

    tool_calls: int = 0
    redundant_tool_calls: int = 0
    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # None means no provider ever reported cache usage for this turn, which is
    # not the same as "reported zero cached tokens".
    cached_input_tokens: int | None = None
    _succeeded: set[str] = field(default_factory=set, repr=False)
    _pending_input: int | None = field(default=None, repr=False)
    _pending_output: int | None = field(default=None, repr=False)
    _pending_cached: int | None = field(default=None, repr=False)

    def note_call(self, name: str, arguments: Mapping[str, object], status: str) -> None:
        """Record one tool invocation and whether it repeated earlier work.

        Only a *successful* repetition counts as redundant. A repeated failure
        is already short-circuited by the runtime before the tool runs, so
        counting it would charge the turn for work it never did.
        """
        self.tool_calls += 1
        key = signature(name, arguments)
        if status == "succeeded":
            if key in self._succeeded:
                self.redundant_tool_calls += 1
            self._succeeded.add(key)

    def note_iteration(self) -> None:
        self.iterations += 1

    def note_usage(self, *, input_tokens: int | None, output_tokens: int | None, cached_input_tokens: int | None) -> None:
        """Keep the latest known value per field within one provider call.

        Some streaming protocols send input usage at the start of a call and
        output usage at the end, so the fields arrive separately and each one
        must survive an update that does not mention it.
        """
        if input_tokens is not None:
            self._pending_input = input_tokens
        if output_tokens is not None:
            self._pending_output = output_tokens
        if cached_input_tokens is not None:
            self._pending_cached = cached_input_tokens

    def settle_provider_call(self) -> None:
        """Fold one provider call's usage into the turn total.

        Called once per provider call rather than per usage event, because a
        protocol that reports cumulative snapshots would otherwise be added to
        itself several times.
        """
        self.input_tokens += self._pending_input or 0
        self.output_tokens += self._pending_output or 0
        if self._pending_cached is not None:
            self.cached_input_tokens = (self.cached_input_tokens or 0) + self._pending_cached
        self._pending_input = self._pending_output = self._pending_cached = None

    def as_row(self) -> dict[str, object]:
        return {
            "tool_calls": self.tool_calls,
            "redundant_tool_calls": self.redundant_tool_calls,
            "iterations": self.iterations,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
        }


__all__ = ["TurnQualityCounters", "signature"]
