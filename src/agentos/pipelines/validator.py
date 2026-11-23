"""Pipeline validation in two stages.

``validate_structure`` is pure: text in, definition and report out. ``validate_context``
checks the definition against the live system (agents, models, flags) through a
``ValidationContext`` supplied by the caller, so it needs no import of session code.
Both run when a file is loaded and again when a run starts.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from .errors import PipelineLoadError, ReportBuilder, ValidationReport
from .model import (
    AgentStep,
    GateStep,
    PipelineDefinition,
    Source,
    VerifierGate,
    compute_definition_hash,
)
from .references import check_references
from .structure import StructureBuilder
from .yaml_loader import LoadedYaml, load_yaml


def validate_structure(text: str, source: Source | str = "user") -> tuple[PipelineDefinition | None, ValidationReport]:
    """Parse and validate a pipeline file. The definition is ``None`` when there is any error."""
    try:
        loaded = load_yaml(text)
    except PipelineLoadError as exc:
        return None, ValidationReport(errors=(exc.error,))
    rb = ReportBuilder()
    sb = StructureBuilder(loaded, rb)
    parts = sb.build()
    if parts is None:
        return None, rb.build()
    budget = parts["budget"]
    check_references(
        sb,
        parts["inputs"],
        parts["defaults"],
        parts["steps"],
        budget.max_tokens if budget is not None else None,
        budget.max_cost_usd if budget is not None else None,
    )
    report = _dedupe(rb.build())
    if report.errors:
        return None, report
    definition = PipelineDefinition(
        version=1,
        id=parts["id"],
        name=parts["name"],
        description=parts["description"],
        inputs=parts["inputs"],
        budget=budget,
        defaults=parts["defaults"],
        steps=parts["steps"],
        source=source if source in ("builtin", "user", "project") else "user",  # type: ignore[arg-type]
        definition_hash=compute_definition_hash(text),
        locations=_locations(loaded),
    )
    return definition, report


def _locations(loaded: LoadedYaml) -> dict[str, tuple[int, int]]:
    table = dict(loaded.positions)
    for path, position in loaded.key_positions.items():
        table[f"{path}#key"] = position
    return table


def _dedupe(report: ValidationReport) -> ValidationReport:
    def unique(items):  # type: ignore[no-untyped-def]
        seen: set[tuple[str, int | None, str]] = set()
        kept = []
        for item in items:
            marker = (item.code, item.line, item.message)
            if marker in seen:
                continue
            seen.add(marker)
            kept.append(item)
        return tuple(kept)

    return ValidationReport(unique(report.errors), unique(report.warnings))


# ----------------------------------------------------------------------- context
EffortLookup = Callable[[str, str], Sequence[str] | None]


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """What the live system offers; every field has a permissive default so tests can pass only what they probe."""

    pipelines_enabled: bool = True
    known_agents: frozenset[str] | None = None  # None: do not check agents
    models: Mapping[str, frozenset[str]] | None = None  # provider -> model ids; None: do not check models
    supported_efforts: EffortLookup | None = None  # (provider, model) -> levels, None when unknown
    worktree_enabled: bool = True
    is_git: bool = True
    verifier_available: bool = True
    default_provider: str | None = None
    extras: Mapping[str, object] = field(default_factory=dict)


def validate_context(definition: PipelineDefinition, ctx: ValidationContext) -> ValidationReport:
    rb = ReportBuilder()

    def where(path: str, key: bool = False) -> tuple[int | None, int | None]:
        if key:
            position = definition.locations.get(f"{path}#key")
            if position is not None:
                return position
        return definition.where(path)

    def error(code: str, message: str, path: str, key: bool = False) -> None:
        rb.error(code, message, where(path, key), path)

    if not ctx.pipelines_enabled:
        error("E_DISABLED", "os pipelines estão desligados (`pipelines.enabled`)", "")
        return rb.build()
    defaults = definition.defaults
    for step in definition.steps:
        here = f"steps[{step.index}]"
        if isinstance(step, AgentStep):
            _check_agent_step(definition, step, here, ctx, error, rb, where)
        elif isinstance(step, GateStep):
            for index, item in enumerate(step.gate.items):
                if isinstance(item, VerifierGate) and not ctx.verifier_available:
                    error(
                        "E_MODEL_UNKNOWN",
                        "o gate `verifier` não tem modelo disponível para revisar (conecte outro provedor ou escolha `policy: same_model`)",
                        f"{here}.gate.{step.gate.mode}[{index}].verifier",
                        key=True,
                    )
    del defaults
    return rb.build()


def _check_agent_step(definition, step, here, ctx, error, rb, where) -> None:  # type: ignore[no-untyped-def]
    defaults = definition.defaults
    agent = step.agent
    if ctx.known_agents is not None and agent and "${{" not in agent and agent not in ctx.known_agents:
        listing = ", ".join(sorted(ctx.known_agents)) or "nenhum"
        error("E_AGENT_UNKNOWN", f"o agente `{agent}` não existe; agentes disponíveis: {listing}", f"{here}.agent")
    provider = step.provider or defaults.provider or ctx.default_provider
    model = step.model or defaults.model
    effort = step.effort or defaults.effort
    templated = any("${{" in str(value or "") for value in (provider, model))
    if ctx.models is not None and not templated:
        if provider and provider not in ctx.models:
            error("E_MODEL_UNKNOWN", f"o provedor `{provider}` não está configurado; provedores: {', '.join(sorted(ctx.models)) or 'nenhum'}", f"{here}.provider" if step.provider else "defaults.provider")
        elif model:
            candidates = ctx.models.get(provider, frozenset()) if provider else frozenset().union(*ctx.models.values())
            if model not in candidates:
                path = f"{here}.model" if step.model else "defaults.model"
                error("E_MODEL_UNKNOWN", f"o modelo `{model}` não está disponível{f' em `{provider}`' if provider else ''}", path)
    if effort and ctx.supported_efforts is not None and provider and model and not templated and "${{" not in effort:
        levels = ctx.supported_efforts(provider, model)
        if levels is not None and effort not in levels:
            rb.warn(
                "W_EFFORT_ADJUSTED",
                f"o modelo `{model}` não aceita esforço `{effort}`; o Orin vai ajustar para o nível mais próximo",
                where(f"{here}.effort" if step.effort else "defaults.effort"),
                f"{here}.effort",
            )
    if step.isolation == "worktree":
        if not ctx.worktree_enabled:
            error(
                "E_ISOLATION_DISABLED",
                "`isolation: worktree` exige `orchestration.worktree.enabled` ligado nas configurações",
                f"{here}.isolation",
                key=True,
            )
        elif step.map is not None and step.map.max_parallel > 1 and not ctx.is_git:
            error(
                "E_ISOLATION_UNAVAILABLE",
                "`isolation: worktree` com `map` paralelo exige um repositório Git; esta pasta não é um",
                f"{here}.isolation",
                key=True,
            )


__all__ = ["ValidationContext", "validate_context", "validate_structure"]
