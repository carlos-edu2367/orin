"""Settings that bound a Code-mode turn. A repository may lower a ceiling, never raise it."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

DEFAULT_SAFETY_MAX_TOOL_ROUNDS = 500
MAX_SAFETY_MAX_TOOL_ROUNDS = 10_000
OPERATOR_LAYERS = frozenset({"user", "cli", "managed"})


class BudgetMeter:
    """What one turn has spent, shared by its main runtime and every subagent runtime (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = Decimal("0")

    def add(self, amount: object) -> None:
        if amount is None:
            return
        try:
            value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
        except (InvalidOperation, ValueError):
            return
        if not value.is_finite() or value <= 0:  # NaN cannot be ordered: check finiteness first
            return
        with self._lock:
            self._total += value

    @property
    def total(self) -> Decimal:
        with self._lock:
            return self._total


def _get(data: Mapping[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in (piece for piece in dotted.split(".") if piece):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def layer_values(settings: object, dotted: str) -> list[tuple[str, Any]]:
    """``(layer, value)`` for every layer that sets ``dotted``, lowest precedence first."""
    found: list[tuple[str, Any]] = []
    for layer in getattr(settings, "layers", ()) or ():
        value = _get(getattr(layer, "data", {}) or {}, dotted)
        if value is not None:
            found.append((str(getattr(layer, "name", "")), value))
    return found


def operator_value(settings: object, dotted: str) -> Any:
    """The highest-precedence value set by the user, a CLI flag or managed policy; never a repository's."""
    value: Any = None
    for name, item in layer_values(settings, dotted):
        if name in OPERATOR_LAYERS:
            value = item
    return value


def positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def positive_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def restricted_minimum(settings: object, dotted: str, parse: Callable[[Any], Any], default: Any = None) -> Any:
    """The operator's value (or ``default``), lowered — never raised — by any repository value."""
    operator: Any = None
    repository: list[Any] = []
    for name, raw in layer_values(settings, dotted):
        parsed = parse(raw)
        if parsed is None:
            continue
        if name in OPERATOR_LAYERS:
            operator = parsed
        else:
            repository.append(parsed)
    base = operator if operator is not None else default
    candidates = [value for value in (base, *repository) if value is not None]
    return min(candidates) if candidates else None


@dataclass(frozen=True, slots=True)
class CodeLoopSettings:
    max_turns: int | None
    max_budget_usd: Decimal | None
    safety_max_tool_rounds: int


def code_loop_settings(settings: object) -> CodeLoopSettings:
    safety = restricted_minimum(settings, "safetyMaxToolRounds", positive_int, DEFAULT_SAFETY_MAX_TOOL_ROUNDS)
    return CodeLoopSettings(
        max_turns=restricted_minimum(settings, "maxTurns", positive_int),
        max_budget_usd=restricted_minimum(settings, "maxBudgetUsd", positive_decimal),
        safety_max_tool_rounds=min(int(safety or DEFAULT_SAFETY_MAX_TOOL_ROUNDS), MAX_SAFETY_MAX_TOOL_ROUNDS),
    )


__all__ = [
    "CodeLoopSettings", "DEFAULT_SAFETY_MAX_TOOL_ROUNDS", "MAX_SAFETY_MAX_TOOL_ROUNDS", "OPERATOR_LAYERS",
    "code_loop_settings", "layer_values", "operator_value", "positive_decimal", "positive_int", "restricted_minimum",
]
