"""Static origin classification of expression paths (decision D5, spec 7.4).

``inputs.*`` is typed by the person who starts the run: TRUSTED. ``steps.*`` is model
output: UNTRUSTED. A ``map`` variable inherits the origin of its ``over``. Fields that
become a command or choose an agent or model accept TRUSTED origins only.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class Origin(str, Enum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


# Fields whose value is executed or selects what runs: only trusted origins may flow in.
TRUSTED_ONLY_FIELDS = frozenset({"gate.command", "agent", "provider", "model", "effort"})


@dataclass(frozen=True, slots=True)
class TaintScope:
    """Origins of the ``map`` variables visible at a given point."""

    map_vars: Mapping[str, Origin] = field(default_factory=dict)


def classify(segments: tuple[str, ...], scope: TaintScope | None = None) -> Origin:
    """Origin of a path. Unknown roots are UNTRUSTED (the validator reports them separately)."""
    if not segments:
        return Origin.UNTRUSTED
    root = segments[0]
    if root == "inputs":
        return Origin.TRUSTED
    if scope is not None and root in scope.map_vars:
        return scope.map_vars[root]
    return Origin.UNTRUSTED


def over_origin(over_segments: tuple[str, ...] | None, scope: TaintScope | None = None) -> Origin:
    """Origin of a ``map`` variable: literal lists are trusted, references follow their path."""
    if over_segments is None:
        return Origin.TRUSTED
    return classify(over_segments, scope)


def requires_trusted(field_path: str) -> bool:
    return field_path in TRUSTED_ONLY_FIELDS


__all__ = [
    "Origin",
    "TRUSTED_ONLY_FIELDS",
    "TaintScope",
    "classify",
    "over_origin",
    "requires_trusted",
]
