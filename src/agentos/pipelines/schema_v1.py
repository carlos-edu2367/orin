"""Declarative field tables of pipeline schema v1.

One table per level. The structural validator walks them; the documentation sync
test and the editor (key suggestions) read the same tables.
"""
from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = 1

# Closed vocabularies
INPUT_TYPES = ("string", "integer", "boolean", "models")
RETRY_CAUSES = ("failed", "stalled", "schema_invalid", "gate_failed")
ISOLATIONS = ("none", "worktree")
EXPECTS = ("zero", "nonzero")
VERIFIER_POLICIES = ("cross_family", "same_model", "explicit")
VERDICTS = ("pass", "fail", "inconclusive")
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max", "ultra")
STEP_FIELDS_OF_REFERENCE = ("output", "failed", "findings")

# Limits
MAX_MAP_ITEMS = 50
MAX_PARALLEL_RANGE = (1, 8)
MAX_RETRIES_RANGE = (0, 3)
MAX_LOOPS_RANGE = (1, 5)
TIMEOUT_RANGE = (1, 600)  # the Bash tool ceiling (gates run through it, D4)
DEFAULT_TIMEOUT_SECONDS = 600
STALL_MINUTES_RANGE = (1, 1440)
INPUT_MAX_LENGTH_RANGE = (1, 100_000)
OUTPUT_SCHEMA_MAX_BYTES = 16 * 1024
MODELS_INPUT_MAX = 3
PIPELINE_ID_MAX = 64
DEFAULT_MAP_PARALLEL = 2

# Default pipeline budget (decision G8)
DEFAULT_BUDGET_TOKENS = 2_000_000
DEFAULT_BUDGET_COST_USD = 15.0
DEFAULT_BUDGET_MINUTES = 120


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    kind: str  # str | int | number | bool | list | map | any | text | step_on_fail
    required: bool = False
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    doc: str = ""


def _f(name: str, kind: str, required: bool = False, **kw: object) -> FieldSpec:
    return FieldSpec(name, kind, required, **kw)  # type: ignore[arg-type]


TOP = (
    _f("version", "int", True, doc="Sempre 1."),
    _f("id", "str", True, doc="Identificador do pipeline: letras minúsculas, números e hífen."),
    _f("name", "str", doc="Nome mostrado na interface."),
    _f("description", "str", doc="Descrição curta."),
    _f("inputs", "map", doc="Entradas digitadas ao iniciar."),
    _f("budget", "map", doc="Teto do pipeline inteiro."),
    _f("defaults", "map", doc="Valores padrão das etapas."),
    _f("steps", "list", True, doc="Etapas, na ordem de execução."),
)

INPUT = (
    _f("type", "str", choices=INPUT_TYPES),
    _f("required", "bool"),
    _f("default", "any"),
    _f("max_length", "int", minimum=INPUT_MAX_LENGTH_RANGE[0], maximum=INPUT_MAX_LENGTH_RANGE[1]),
)

BUDGET = (
    _f("max_tokens", "int", minimum=1),
    _f("max_cost_usd", "number", minimum=0),
    _f("max_minutes", "number", minimum=0),
)

DEFAULTS = (
    _f("provider", "text"),
    _f("model", "text"),
    _f("effort", "text"),
    _f("retries", "map"),
    _f("stall_minutes", "int", minimum=STALL_MINUTES_RANGE[0], maximum=STALL_MINUTES_RANGE[1]),
)

RETRIES = (
    _f("max", "int", minimum=MAX_RETRIES_RANGE[0], maximum=MAX_RETRIES_RANGE[1]),
    _f("on", "list"),
)

STEP = (
    _f("id", "str", True),
    _f("name", "str"),
    _f("description", "str"),
    _f("agent", "text"),
    _f("gate", "map"),
    _f("prompt", "text"),
    _f("provider", "text"),
    _f("model", "text"),
    _f("effort", "text"),
    _f("isolation", "str", choices=ISOLATIONS),
    _f("map", "map"),
    _f("output_schema", "map"),
    _f("budget", "map"),
    _f("when", "text"),
    _f("on_fail", "step_on_fail"),
    _f("next", "str"),
    _f("retries", "map"),
    _f("stall_minutes", "int", minimum=STALL_MINUTES_RANGE[0], maximum=STALL_MINUTES_RANGE[1]),
)

MAP = (
    _f("over", "any", True),
    _f("as", "str", True),
    _f("max_parallel", "int", minimum=MAX_PARALLEL_RANGE[0], maximum=MAX_PARALLEL_RANGE[1]),
    _f("fail_fast", "bool"),
)

GATE = (
    _f("all", "list"),
    _f("any", "list"),
)

ON_FAIL = (
    _f("goto", "str"),
    _f("max_loops", "int", minimum=MAX_LOOPS_RANGE[0], maximum=MAX_LOOPS_RANGE[1]),
)

COMMAND_ITEM = (
    _f("command", "text", True),
    _f("expect", "str", choices=EXPECTS),
    _f("timeout_seconds", "int", minimum=TIMEOUT_RANGE[0], maximum=TIMEOUT_RANGE[1]),
)

SCHEMA_ITEM = (
    _f("step", "str", True),
    _f("json_schema", "map", True),
)

VERIFIER_ITEM = (
    _f("policy", "str", choices=VERIFIER_POLICIES),
    _f("effort", "str", choices=EFFORT_LEVELS),
    _f("provider", "str"),
    _f("model", "str"),
    _f("pass_on", "list"),
)

# Keys that identify a gate item and the table that describes the rest of its keys
GATE_ITEM_KINDS = ("command", "schema", "verifier")

TABLES: dict[str, tuple[FieldSpec, ...]] = {
    "top": TOP,
    "input": INPUT,
    "budget": BUDGET,
    "defaults": DEFAULTS,
    "retries": RETRIES,
    "step": STEP,
    "map": MAP,
    "gate": GATE,
    "on_fail": ON_FAIL,
    "command_item": COMMAND_ITEM,
    "schema_item": SCHEMA_ITEM,
    "verifier_item": VERIFIER_ITEM,
}


def names(table: str) -> tuple[str, ...]:
    return tuple(item.name for item in TABLES[table])


def spec_of(table: str, name: str) -> FieldSpec | None:
    for item in TABLES[table]:
        if item.name == name:
            return item
    return None


__all__ = [name for name in dir() if name.isupper() or name in {"FieldSpec", "TABLES", "names", "spec_of"}]
