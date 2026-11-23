"""Immutable model of a validated pipeline definition (schema v1)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
from typing import Literal

Source = Literal["builtin", "user", "project"]


def normalize_text(text: str) -> str:
    """Line endings as ``\\n``; the form hashed and stored."""
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def compute_definition_hash(text: str) -> str:
    """SHA-256 of the normalized (``\\r\\n`` to ``\\n``) UTF-8 text of a pipeline file."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class InputSpec:
    name: str
    type: str = "string"  # string | integer | boolean | models
    required: bool = False
    default: object = None
    max_length: int | None = None


@dataclass(frozen=True, slots=True)
class Budget:
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_minutes: float | None = None

    @property
    def is_empty(self) -> bool:
        return self.max_tokens is None and self.max_cost_usd is None and self.max_minutes is None


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max: int = 0
    on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Defaults:
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    retries: RetryPolicy | None = None
    stall_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class MapSpec:
    over: str | None  # raw reference text (``${{ path }}`` or bare path); None when ``literal`` is used
    var: str
    max_parallel: int = 2
    fail_fast: bool = True
    literal: tuple[object, ...] | None = None


@dataclass(frozen=True, slots=True)
class OnFail:
    action: Literal["stop", "continue", "goto"] = "stop"
    goto: str | None = None
    max_loops: int | None = None


@dataclass(frozen=True, slots=True)
class CommandGate:
    command: str
    expect: Literal["zero", "nonzero"] = "zero"
    timeout_seconds: int = 600


@dataclass(frozen=True, slots=True)
class SchemaGate:
    step: str
    json_schema: Mapping[str, object] = field(default_factory=dict, hash=False)


@dataclass(frozen=True, slots=True)
class VerifierGate:
    policy: str | None = None
    effort: str | None = None
    provider: str | None = None
    model: str | None = None
    pass_on: tuple[str, ...] = ("pass",)


GateItem = CommandGate | SchemaGate | VerifierGate


@dataclass(frozen=True, slots=True)
class Gate:
    mode: Literal["all", "any"]
    items: tuple[GateItem, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Step:
    """Fields common to every step kind."""

    id: str
    name: str | None = None
    description: str | None = None
    when: str | None = None
    on_fail: OnFail = field(default_factory=OnFail)
    next: str | None = None
    retries: RetryPolicy | None = None
    stall_minutes: int | None = None
    budget: Budget | None = None
    index: int = 0

    @property
    def kind(self) -> str:
        return "gate" if isinstance(self, GateStep) else "agent"


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentStep(Step):
    agent: str
    prompt: str
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    isolation: Literal["none", "worktree"] | None = None
    map: MapSpec | None = None
    output_schema: Mapping[str, object] | None = field(default=None, hash=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class GateStep(Step):
    gate: Gate


@dataclass(frozen=True, slots=True)
class PipelineDefinition:
    version: int
    id: str
    name: str
    description: str
    inputs: tuple[InputSpec, ...]
    budget: Budget | None
    defaults: Defaults
    steps: tuple[Step, ...]
    source: Source = "user"
    definition_hash: str = ""
    # Positions (line, column) of YAML paths, used by the context stage; not part of identity.
    locations: Mapping[str, tuple[int, int]] = field(default_factory=dict, compare=False, hash=False, repr=False)

    def step(self, step_id: str) -> Step | None:
        for item in self.steps:
            if item.id == step_id:
                return item
        return None

    def input(self, name: str) -> InputSpec | None:
        for item in self.inputs:
            if item.name == name:
                return item
        return None

    def where(self, path: str) -> tuple[int | None, int | None]:
        current = path
        while True:
            if current in self.locations:
                return self.locations[current]
            if not current:
                return (None, None)
            cut = max(current.rfind("."), current.rfind("["))
            current = current[:cut] if cut > 0 else ""


__all__ = [
    "AgentStep",
    "Budget",
    "CommandGate",
    "Defaults",
    "Gate",
    "GateItem",
    "GateStep",
    "InputSpec",
    "MapSpec",
    "OnFail",
    "PipelineDefinition",
    "RetryPolicy",
    "SchemaGate",
    "Source",
    "Step",
    "VerifierGate",
    "compute_definition_hash",
    "normalize_text",
]
