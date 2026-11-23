"""Gate evaluation: command, schema and verifier items (WS-C6, decisions G3, G14, G16, spike S6).

Security (spec 7.4 S2): a gate command is built only from the command template in the YAML and ``inputs.*``,
every input value quoted for the shell. Two forms (G16):

* a reference in the middle of the text (``pytest -q ${{ inputs.arquivo }}``): the whole value is ONE quoted
  argument, whatever it contains;
* a reference that is the whole ``command`` (``command: ${{ inputs.testes }}``): the value is split into words
  with ``shlex.split`` and each word is quoted, the first one being the program.

Nothing derived from ``steps.*`` can reach the runner, and ``cmd`` (no safe quoting) refuses any command that
carries an input (``GATE_SHELL_UNSUPPORTED``).
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agentos.agentic.structured_output import validate_instance

from .context import RunContext
from .expressions import ExpressionError, TemplateRef, format_value, split_template
from .model import CommandGate, GateStep, SchemaGate, VerifierGate
from .ports import (
    CancelToken,
    ChangeScopePort,
    CommandResult,
    GateRunner,
    VerifierPort,
    VerifierRequest,
    VerifierResult,
)
from .stall import combine_fingerprints, command_fingerprint, findings_fingerprint

OUTPUT_EXCERPT_CHARS = 4000
_EXIT_NOT_A_TEST_FAILURE = frozenset({0, 126, 127})
_PROVEN_SEVERITIES = frozenset({"blocker", "major"})

GATE_INPUT_INVALID = "GATE_INPUT_INVALID"
GATE_SHELL_UNSUPPORTED = "GATE_SHELL_UNSUPPORTED"
GATE_PERMISSION_DENIED = "GATE_PERMISSION_DENIED"
GATE_VERIFIER_UNAVAILABLE = "GATE_VERIFIER_UNAVAILABLE"
GATE_REF_UNTRUSTED = "GATE_REF_UNTRUSTED"


class GateCommandError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class RenderedCommand:
    text: str
    quoted_values: tuple[str, ...]  # the raw input values that went in (each is exactly one shell word)


def render_command(template: str, inputs: Mapping[str, Any], dialect: str = "posix") -> RenderedCommand:
    """Build the shell command from ``template`` and ``inputs`` only (see the module docstring)."""
    try:
        parts = split_template(template)
    except ExpressionError as exc:
        raise GateCommandError(GATE_INPUT_INVALID, f"comando inválido: {exc.message}") from exc
    refs = [part for part in parts if isinstance(part, TemplateRef)]
    if not refs:
        return RenderedCommand(template, ())
    if dialect != "posix":
        raise GateCommandError(
            GATE_SHELL_UNSUPPORTED,
            "este shell não tem citação segura para entradas em comandos de gate; instale o Git for Windows (Git Bash)",
        )
    for ref in refs:
        segments = ref.path.segments
        if len(segments) != 2 or segments[0] != "inputs":
            raise GateCommandError(GATE_REF_UNTRUSTED, f"`{'.'.join(segments)}` não pode entrar em um comando de gate")
    meaningful = [part for part in parts if not (isinstance(part, str) and not part.strip())]
    if len(meaningful) == 1 and isinstance(meaningful[0], TemplateRef):
        name = refs[0].path.segments[1]
        value = format_value(inputs.get(name)) if name in inputs else ""
        try:
            words = shlex.split(value, posix=True)
        except ValueError as exc:
            raise GateCommandError(GATE_INPUT_INVALID, f"o valor de `{name}` tem aspas sem fechar") from exc
        if not words:
            raise GateCommandError(GATE_INPUT_INVALID, f"o valor de `{name}` está vazio")
        return RenderedCommand(" ".join(shlex.quote(word) for word in words), tuple(words))
    out: list[str] = []
    values: list[str] = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
            continue
        name = part.path.segments[1]
        value = format_value(inputs.get(name)) if name in inputs else ""
        quoted = shlex.quote(value)
        if shlex.split(quoted, posix=True) != [value]:  # defense in depth: a quoted value is always one word
            raise GateCommandError(GATE_INPUT_INVALID, f"o valor de `{name}` não pode ser citado com segurança")
        out.append(quoted)
        values.append(value)
    return RenderedCommand("".join(out), tuple(values))


@dataclass(slots=True)
class GateResult:
    passed: bool
    findings: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    fingerprint: str = ""
    error_code: str | None = None  # set when a failure must not be retried (denied, invalid input ...)
    cancelled: bool = False
    exit_code: int | None = None
    verdict: str | None = None
    gate_kind: str = "command"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    cost_known: bool = True

    def as_output(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": list(self.findings),
            "items": list(self.items),
            "fingerprint": self.fingerprint,
            "error_code": self.error_code,
            "exit_code": self.exit_code,
            "verdict": self.verdict,
            "gate_kind": self.gate_kind,
        }


class GateEvaluator:
    def __init__(
        self,
        runner: GateRunner,
        *,
        run_id: str,
        dialect: str = "posix",
        verifier: VerifierPort | None = None,
        change_scope: ChangeScopePort | None = None,
    ) -> None:
        self._runner = runner
        self._run_id = run_id
        self._dialect = dialect
        self._verifier = verifier
        self._change_scope = change_scope

    def evaluate(self, step: GateStep, context: RunContext, cancel: CancelToken) -> GateResult:
        inputs = context.inputs
        result = GateResult(passed=False)
        outcomes: list[bool] = []
        fingerprints: list[str] = []
        mode = step.gate.mode
        for item in step.gate.items:
            if cancel.is_set():
                result.cancelled = True
                break
            ok, fingerprint = self._run_item(item, step, inputs, context, cancel, result)
            outcomes.append(ok)
            if not ok:
                fingerprints.append(fingerprint)
            if mode == "all" and not ok:
                break
            if mode == "any" and ok:
                break
        result.gate_kind = ",".join(sorted({type(item).__name__.replace("Gate", "").lower() for item in step.gate.items}))
        passed = bool(outcomes) and (all(outcomes) if mode == "all" else any(outcomes)) and not result.cancelled
        result.passed = passed
        if passed:
            result.findings = []  # an ``any`` gate that passed carries no failure to report
            result.error_code = None
            result.fingerprint = ""
        else:
            result.fingerprint = combine_fingerprints([item for item in fingerprints if item])
        return result

    # ------------------------------------------------------------------ items
    def _run_item(
        self,
        item: Any,
        step: GateStep,
        inputs: Mapping[str, Any],
        context: RunContext,
        cancel: CancelToken,
        result: GateResult,
    ) -> tuple[bool, str]:
        if isinstance(item, CommandGate):
            return self._command(item, inputs, cancel, result)
        if isinstance(item, SchemaGate):
            return self._schema(item, context, result)
        if isinstance(item, VerifierGate):
            return self._verifier_item(item, step, cancel, result)
        result.error_code = "GATE_ITEM_UNKNOWN"
        return False, ""

    def _command(
        self, item: CommandGate, inputs: Mapping[str, Any], cancel: CancelToken, result: GateResult
    ) -> tuple[bool, str]:
        try:
            rendered = render_command(item.command, inputs, self._dialect)
        except GateCommandError as exc:
            result.error_code = exc.code
            result.items.append({"kind": "command", "passed": False, "error_code": exc.code, "message": exc.message})
            result.findings.append({"kind": "command_failed", "command": item.command, "exit_code": None, "output_excerpt": exc.message})
            return False, command_fingerprint(None, exc.message)
        outcome: CommandResult = self._runner.run_command(rendered.text, item.timeout_seconds, cancel)
        result.exit_code = outcome.exit_code
        if outcome.cancelled or (cancel.is_set() and outcome.exit_code is None and not outcome.timed_out):
            result.cancelled = True
            result.items.append({"kind": "command", "passed": False, "cancelled": True})
            return False, ""
        excerpt = outcome.output_tail[-OUTPUT_EXCERPT_CHARS:]
        if outcome.denied:
            result.error_code = GATE_PERMISSION_DENIED
            passed = False
        elif outcome.timed_out or outcome.exit_code is None:
            passed = False
            if outcome.error_code in {GATE_SHELL_UNSUPPORTED, GATE_INPUT_INVALID}:
                result.error_code = outcome.error_code
        elif item.expect == "nonzero":
            passed = outcome.exit_code not in _EXIT_NOT_A_TEST_FAILURE
        else:
            passed = outcome.exit_code == 0
        result.items.append(
            {
                "kind": "command",
                "passed": passed,
                "exit_code": outcome.exit_code,
                "timed_out": outcome.timed_out,
                "denied": outcome.denied,
            }
        )
        if passed:
            return True, ""
        result.findings.append(
            {
                "kind": "command_failed",
                "command": rendered.text,
                "exit_code": outcome.exit_code,
                "timed_out": outcome.timed_out,
                "denied": outcome.denied,
                "expected": item.expect,
                "output_excerpt": excerpt,
            }
        )
        return False, command_fingerprint(outcome.exit_code, excerpt)

    def _schema(self, item: SchemaGate, context: RunContext, result: GateResult) -> tuple[bool, str]:
        target = context.result(item.step)
        value = target.output if target is not None else None
        if target is None or target.state in {"skipped", "pending"} or value is None:
            errors = [f"a etapa `{item.step}` não produziu saída"]
        else:
            errors = validate_instance(item.json_schema, value)
        passed = not errors
        result.items.append({"kind": "schema", "passed": passed, "step": item.step, "errors": errors[:10]})
        if passed:
            return True, ""
        result.findings.append({"kind": "schema_invalid", "step": item.step, "errors": errors[:10]})
        return False, combine_fingerprints(["schema", *errors[:10]])

    def _verifier_item(
        self, item: VerifierGate, step: GateStep, cancel: CancelToken, result: GateResult
    ) -> tuple[bool, str]:
        if self._verifier is None:
            result.error_code = GATE_VERIFIER_UNAVAILABLE
            result.items.append({"kind": "verifier", "passed": False, "error_code": GATE_VERIFIER_UNAVAILABLE})
            result.findings.append({"kind": "verifier_unavailable", "claim": "nenhum verificador disponível"})
            return False, "verifier_unavailable"
        scope: tuple[str, ...] = ()
        if self._change_scope is not None:
            scope = tuple(self._change_scope.changed_files())
        explicit = (item.provider, item.model) if item.provider and item.model else None
        request = VerifierRequest(
            policy=item.policy,
            explicit=explicit,
            effort=item.effort,
            pipeline_run_id=self._run_id,
            step_id=step.id,
            scope_files=scope,
        )
        review: VerifierResult = self._verifier.review(request, cancel)
        result.input_tokens += review.input_tokens
        result.output_tokens += review.output_tokens
        if review.cost_usd is None:
            result.cost_known = False
        else:
            result.cost_usd = (result.cost_usd or 0.0) + review.cost_usd
        result.verdict = review.verdict
        if cancel.is_set():
            result.cancelled = True
            return False, ""
        proven = [dict(row) for row in review.findings if _is_proven(row)]
        passed = review.error_code is None and review.verdict in item.pass_on
        result.items.append(
            {
                "kind": "verifier",
                "passed": passed,
                "verdict": review.verdict,
                "review_id": review.review_id,
                "reviewer": review.reviewer,
                "findings": len(proven),
            }
        )
        if passed:
            return True, ""
        if review.error_code is not None:
            result.error_code = review.error_code
        result.findings.extend(proven)
        return False, findings_fingerprint(proven) if proven else combine_fingerprints(["verdict", review.verdict])


def _is_proven(row: Mapping[str, Any]) -> bool:
    return str(row.get("severity", "")).lower() in _PROVEN_SEVERITIES


def summarize_failure(result: GateResult) -> str:
    """One pt-BR line for the journal ``error`` column."""
    if result.error_code == GATE_PERMISSION_DENIED:
        return "permissão negada para o comando do gate"
    parts = []
    for finding in result.findings[:3]:
        if finding.get("kind") == "command_failed":
            parts.append(f"comando falhou (saída {finding.get('exit_code')})")
        elif finding.get("kind") == "schema_invalid":
            parts.append(f"saída da etapa {finding.get('step')} fora do esquema")
        else:
            parts.append(str(finding.get("claim") or finding.get("kind"))[:120])
    return "; ".join(parts) or "gate reprovado"


def gate_input_hash_parts(step: GateStep, inputs: Mapping[str, Any], dialect: str) -> list[Any]:
    """The pieces of a gate step that decide its result, for the journal input hash (no raw secrets beyond inputs)."""
    parts: list[Any] = [step.gate.mode]
    for item in step.gate.items:
        if isinstance(item, CommandGate):
            try:
                text = render_command(item.command, inputs, dialect).text
            except GateCommandError as exc:
                text = f"!{exc.code}"
            parts.append(["command", text, item.expect, item.timeout_seconds])
        elif isinstance(item, SchemaGate):
            parts.append(["schema", item.step, dict(item.json_schema)])
        elif isinstance(item, VerifierGate):
            parts.append(["verifier", item.policy, item.effort, item.provider, item.model, list(item.pass_on)])
    return parts


__all__ = [
    "GATE_INPUT_INVALID",
    "GATE_PERMISSION_DENIED",
    "GATE_REF_UNTRUSTED",
    "GATE_SHELL_UNSUPPORTED",
    "GATE_VERIFIER_UNAVAILABLE",
    "GateCommandError",
    "GateEvaluator",
    "GateResult",
    "OUTPUT_EXCERPT_CHARS",
    "RenderedCommand",
    "gate_input_hash_parts",
    "render_command",
    "summarize_failure",
]
