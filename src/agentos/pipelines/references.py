"""Second structural pass: references, taint and control flow.

Runs on the built steps. Every ``${{ ... }}`` may read ``inputs.*``, the ``map``
variable of its own step and ``steps.<earlier>.(output|failed|findings)``. Fields that
become a command or choose an agent/model reject model-derived origins (spec 7.4).
"""
from __future__ import annotations

from collections.abc import Sequence

from . import schema_v1 as sv
from .expressions import (
    ExpressionError,
    PathRef,
    TemplateRef,
    collect_paths,
    parse_expression,
    split_template,
    unwrap_expression,
)
from .graph import analyze
from .model import AgentStep, Defaults, GateStep, InputSpec, Step
from .structure import StructureBuilder
from .taint import Origin, TaintScope, classify, over_origin, requires_trusted


class _Checker:
    def __init__(self, sb: StructureBuilder, inputs: Sequence[InputSpec], steps: Sequence[Step]) -> None:
        self.sb = sb
        self.input_names = [item.name for item in inputs]
        self.order = {step.id: position for position, step in enumerate(steps)}
        self.names = [step.id for step in steps]

    # ------------------------------------------------------------------ paths
    def check_path(self, segments: tuple[str, ...], current: int, scope: TaintScope, field: str, where: str) -> None:
        sb = self.sb
        root = segments[0]
        if root == "inputs":
            if len(segments) < 2 or segments[1] not in self.input_names:
                listing = ", ".join(self.input_names) or "nenhuma"
                missing = segments[1] if len(segments) > 1 else ""
                sb.error("E_REF_UNKNOWN", f"a entrada `{missing}` não existe; entradas declaradas: {listing}", where)
                return
        elif root == "steps":
            if len(segments) < 3:
                sb.error("E_REF_UNKNOWN", "use `steps.<etapa>.output`, `steps.<etapa>.failed` ou `steps.<etapa>.findings`", where)
                return
            name = segments[1]
            if name not in self.order:
                sb.error("E_REF_UNKNOWN", f"a etapa `{name}` não existe; etapas declaradas: {', '.join(self.names)}", where)
                return
            if self.order[name] >= current:
                sb.error(
                    "E_REF_FORWARD",
                    f"`steps.{name}` aponta para uma etapa que roda depois (ou para a própria etapa); só é possível referenciar etapas anteriores",
                    where,
                )
                return
            if segments[2] not in sv.STEP_FIELDS_OF_REFERENCE:
                sb.error("E_REF_UNKNOWN", f"`{segments[2]}` não existe em uma etapa; use {', '.join(sv.STEP_FIELDS_OF_REFERENCE)}", where)
                return
        elif root not in scope.map_vars:
            sb.error("E_REF_UNKNOWN", f"`{root}` não é uma entrada (`inputs`), uma etapa (`steps`) nem a variável do `map` desta etapa", where)
            return
        if requires_trusted(field) and classify(segments, scope) is Origin.UNTRUSTED:
            sb.error(
                "E_REF_TAINT",
                f"saída de modelo (`{'.'.join(segments)}`) não pode entrar em `{field}`; só `inputs` e valores literais",
                where,
            )

    def check_template(self, text: object, current: int, scope: TaintScope, field: str, where: str) -> None:
        if not isinstance(text, str) or "${{" not in text:
            return
        try:
            parts = split_template(text)
        except ExpressionError as exc:
            self.sb.error("E_EXPR_SYNTAX", f"expressão inválida em `{field}`: {exc.message}", where)
            return
        for part in parts:
            if isinstance(part, TemplateRef):
                self.check_path(part.path.segments, current, scope, field, where)

    def check_when(self, text: object, current: int, scope: TaintScope, where: str) -> None:
        if not isinstance(text, str):
            return
        try:
            expr = parse_expression(unwrap_expression(text))
        except ExpressionError as exc:
            self.sb.error("E_EXPR_SYNTAX", f"`when` inválido: {exc.message}", where)
            return
        for path in collect_paths(expr):
            self.check_path(path.segments, current, scope, "when", where)

    # ------------------------------------------------------------------ steps
    def check_step(self, step: Step, current: int) -> None:
        here = f"steps[{step.index}]"
        scope = TaintScope()
        if isinstance(step, AgentStep) and step.map is not None:
            origin = Origin.TRUSTED
            if step.map.over is not None:
                try:
                    over = parse_expression(unwrap_expression(step.map.over))
                except ExpressionError:
                    over = None
                if isinstance(over, PathRef):
                    self.check_path(over.segments, current, scope, "map.over", f"{here}.map.over")
                    origin = over_origin(over.segments, scope)
                else:
                    origin = Origin.UNTRUSTED
            scope = TaintScope({step.map.var: origin})
        if step.when is not None:
            self.check_when(step.when, current, scope, f"{here}.when")
        if step.next is not None and step.next not in self.order:
            self.sb.error("E_REF_UNKNOWN", f"`next` aponta para a etapa `{step.next}`, que não existe; etapas declaradas: {', '.join(self.names)}", f"{here}.next")
        goto = step.on_fail.goto
        if goto is not None and goto not in self.order:
            self.sb.error("E_REF_UNKNOWN", f"`goto` aponta para a etapa `{goto}`, que não existe; etapas declaradas: {', '.join(self.names)}", f"{here}.on_fail.goto")
        if isinstance(step, AgentStep):
            for name in ("agent", "provider", "model", "effort"):
                self.check_template(getattr(step, name), current, scope, name, f"{here}.{name}")
            self.check_template(step.prompt, current, scope, "prompt", f"{here}.prompt")
        elif isinstance(step, GateStep):
            for index, item in enumerate(step.gate.items):
                where = f"{here}.gate.{step.gate.mode}[{index}]"
                command = getattr(item, "command", None)
                if command is not None:
                    self.check_template(command, current, scope, "gate.command", f"{where}.command")
                target = getattr(item, "step", None)
                if target is not None:
                    self.check_gate_target(target, current, f"{where}.schema.step")

    def check_gate_target(self, target: str, current: int, where: str) -> None:
        if target not in self.order:
            self.sb.error("E_REF_UNKNOWN", f"o gate `schema` valida a etapa `{target}`, que não existe; etapas declaradas: {', '.join(self.names)}", where)
        elif self.order[target] >= current:
            self.sb.error("E_REF_FORWARD", f"o gate `schema` só valida etapas anteriores (`{target}` roda depois)", where)


def check_references(
    sb: StructureBuilder,
    inputs: Sequence[InputSpec],
    defaults: Defaults,
    steps: Sequence[Step],
    pipeline_budget_tokens: int | None,
    pipeline_budget_cost: float | None,
) -> None:
    checker = _Checker(sb, inputs, steps)
    for name in ("provider", "model", "effort"):
        checker.check_template(getattr(defaults, name), -1, TaintScope(), name, f"defaults.{name}")
    for current, step in enumerate(steps):
        checker.check_step(step, current)
    _check_graph(sb, steps)
    _check_fanout(sb, steps, pipeline_budget_tokens, pipeline_budget_cost)


def _check_graph(sb: StructureBuilder, steps: Sequence[Step]) -> None:
    result = analyze(steps)
    for loop in result.unbounded:
        source, target = steps[loop.source], steps[loop.target]
        if loop.kind == "goto" and source.on_fail.max_loops is None:
            continue  # already reported by the structural pass (goto without a valid max_loops)
        field = "next" if loop.kind == "next" else "on_fail"
        sb.error(
            "E_LOOP_NO_MAX",
            f"a etapa `{source.id}` volta para `{target.id}` e forma um laço sem parada; "
            f"coloque `on_fail: {{goto: <etapa>, max_loops: N}}` (N de {sv.MAX_LOOPS_RANGE[0]} a {sv.MAX_LOOPS_RANGE[1]}) em uma etapa do ciclo",
            f"steps[{source.index}].{field}",
            key=True,
        )
    for position in result.unreachable:
        step = steps[position]
        sb.warn("W_UNREACHABLE_STEP", f"a etapa `{step.id}` nunca é alcançada", f"steps[{step.index}].id")


def _check_fanout(
    sb: StructureBuilder, steps: Sequence[Step], total_tokens: int | None, total_cost: float | None
) -> None:
    for step in steps:
        if not isinstance(step, AgentStep) or step.map is None or step.budget is None:
            continue
        items = len(step.map.literal) if step.map.literal is not None else sv.MAX_MAP_ITEMS
        over_tokens = total_tokens is not None and step.budget.max_tokens is not None and step.budget.max_tokens * items > total_tokens
        over_cost = total_cost is not None and step.budget.max_cost_usd is not None and step.budget.max_cost_usd * items > total_cost
        if over_tokens or over_cost:
            sb.warn(
                "W_BUDGET_FANOUT",
                f"o teto por agente da etapa `{step.id}` multiplicado por até {items} itens passa do `budget` do pipeline; "
                "o teto do pipeline continua sendo o limite, e itens finais podem não rodar",
                f"steps[{step.index}].budget",
                key=True,
            )


__all__ = ["check_references"]
