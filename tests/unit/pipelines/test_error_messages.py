"""Snapshot of the pt-BR messages: `linha N, coluna M: <mensagem> (código E_...)` (AC1)."""
from __future__ import annotations

import pytest

from agentos.pipelines.errors import ERROR_CODES, WARNING_CODES
from agentos.pipelines.validator import ValidationContext, validate_context, validate_structure

from .helpers import HEADER, fixture_text, pipeline

AGENT = "- id: a\n  agent: general\n  prompt: x\n"


def _doc(steps: str, header: str = HEADER) -> str:
    return pipeline(steps, header=header)


def _step(extra: str) -> str:
    return AGENT + extra


# code -> (document, context-or-None). Structure cases run validate_structure only; context cases also validate_context.
CASES: dict[str, tuple[str, ValidationContext | None]] = {
    "E_YAML_SYNTAX": ("version: 1\nid: t\nsteps: [\n", None),
    "E_YAML_FORBIDDEN": ("version: 1\nid: t\nsteps: &x []\nother: *x\n", None),
    "E_TOO_LARGE": ("a: " + "x" * (256 * 1024 + 1), None),
    "E_VERSION": ("id: t\nsteps: []\n", None),
    "E_ID": ("version: 1\nid: Bad_ID\nsteps: []\n", None),
    "E_UNKNOWN_FIELD": (_doc(_step("  retires: {max: 1}\n")), None),
    "E_TYPE": (_doc(_step("  stall_minutes: abc\n")), None),
    "E_REQUIRED": (_doc("- id: a\n  agent: general\n"), None),
    "E_STEP_ID_DUP": (_doc(AGENT + AGENT), None),
    "E_STEP_KIND": (_doc("- id: a\n  prompt: x\n"), None),
    "E_REF_UNKNOWN": (_doc(_step("- id: b\n  agent: general\n  prompt: ${{ steps.nada.output }}\n")), None),
    "E_REF_FORWARD": (_doc("- id: a\n  agent: general\n  prompt: ${{ steps.b.output }}\n- id: b\n  agent: general\n  prompt: x\n"), None),
    "E_REF_TAINT": (_doc(_step("- id: g\n  gate: {all: [{command: '${{ steps.a.output.cmd }}'}]}\n")), None),
    "E_EXPR_SYNTAX": (_doc(_step("  when: ${{ inputs.arquivo + 1 }}\n")), None),
    "E_LOOP_NO_MAX": (_doc(AGENT + "- id: b\n  agent: general\n  prompt: x\n  next: a\n"), None),
    "E_MAP_RANGE": (_doc(_step("  map: {over: [1], as: i, max_parallel: 9}\n")), None),
    "E_AGENT_UNKNOWN": (_doc("- id: a\n  agent: fantasma\n  prompt: x\n"), ValidationContext(known_agents=frozenset({"general"}))),
    "E_MODEL_UNKNOWN": (_doc("- id: a\n  agent: general\n  prompt: x\n  provider: nuvem\n"), ValidationContext(models={"anthropic": frozenset({"m"})})),
    "E_ISOLATION_DISABLED": (_doc(_step("  isolation: worktree\n")), ValidationContext(worktree_enabled=False)),
    "E_ISOLATION_UNAVAILABLE": (
        _doc(_step("  isolation: worktree\n  map: {over: [1, 2], as: i, max_parallel: 2}\n")),
        ValidationContext(is_git=False),
    ),
    "E_SCHEMA_INVALID": (_doc(_step("  output_schema: {type: banana}\n")), None),
    "E_BUDGET": (_doc(_step("  budget: {max_tokens: -5}\n")), None),
    "E_RETRY": (_doc(_step("  retries: {max: 4}\n")), None),
    "E_INPUT": ("version: 1\nid: t\ninputs:\n  x: {type: banana}\nsteps:\n  - id: a\n    agent: general\n    prompt: x\n", None),
    "E_DISABLED": (_doc(AGENT), ValidationContext(pipelines_enabled=False)),
}

WARNING_CASES: dict[str, str] = {
    "W_BUDGET_FANOUT": fixture_text("implementar-verificar.yaml"),
    "W_BUDGET_DEFAULT": "version: 1\nid: t\nname: T\nsteps:\n  - id: a\n    agent: general\n    prompt: x\n",
    "W_UNREACHABLE_STEP": _doc(AGENT.replace("prompt: x\n", "prompt: x\n  next: c\n") + "- id: b\n  agent: general\n  prompt: x\n- id: c\n  agent: general\n  prompt: x\n"),
}

# Filled by reviewing the output once; any wording change must be a conscious edit here.
EXPECTED: dict[str, str] = {
    "E_AGENT_UNKNOWN": "linha 11, coluna 12: o agente `fantasma` não existe; agentes disponíveis: general (código E_AGENT_UNKNOWN)",
    "E_BUDGET": "linha 13, coluna 26: `max_tokens` deve ser um número inteiro maior que zero (código E_BUDGET)",
    "E_DISABLED": "linha 1, coluna 1: os pipelines estão desligados (`pipelines.enabled`) (código E_DISABLED)",
    "E_EXPR_SYNTAX": "linha 13, coluna 11: `when` inválido: caractere não permitido `+` (código E_EXPR_SYNTAX)",
    "E_ID": "linha 2, coluna 5: `id` deve ter de 1 a 64 caracteres entre letras minúsculas, números e hífen (código E_ID)",
    "E_INPUT": "linha 4, coluna 13: tipo de entrada desconhecido `banana` (use string, integer, boolean, models) (código E_INPUT)",
    "E_ISOLATION_DISABLED": "linha 13, coluna 5: `isolation: worktree` exige `orchestration.worktree.enabled` ligado nas configurações (código E_ISOLATION_DISABLED)",
    "E_ISOLATION_UNAVAILABLE": "linha 13, coluna 5: `isolation: worktree` com `map` paralelo exige um repositório Git; esta pasta não é um (código E_ISOLATION_UNAVAILABLE)",
    "E_LOOP_NO_MAX": "linha 16, coluna 5: a etapa `b` volta para `a` e forma um laço sem parada; coloque `on_fail: {goto: <etapa>, max_loops: N}` (N de 1 a 5) em uma etapa do ciclo (código E_LOOP_NO_MAX)",
    "E_MAP_RANGE": "linha 13, coluna 43: `max_parallel` deve ser um inteiro de 1 a 8 (código E_MAP_RANGE)",
    "E_MODEL_UNKNOWN": "linha 13, coluna 15: o provedor `nuvem` não está configurado; provedores: anthropic (código E_MODEL_UNKNOWN)",
    "E_REF_FORWARD": "linha 12, coluna 13: `steps.b` aponta para uma etapa que roda depois (ou para a própria etapa); só é possível referenciar etapas anteriores (código E_REF_FORWARD)",
    "E_REF_TAINT": "linha 14, coluna 28: saída de modelo (`steps.a.output.cmd`) não pode entrar em `gate.command`; só `inputs` e valores literais (código E_REF_TAINT)",
    "E_REF_UNKNOWN": "linha 15, coluna 13: a etapa `nada` não existe; etapas declaradas: a, b (código E_REF_UNKNOWN)",
    "E_REQUIRED": "linha 10, coluna 5: a etapa de agente precisa de `prompt` (código E_REQUIRED)",
    "E_RETRY": "linha 13, coluna 20: `retries.max` deve ser um inteiro de 0 a 3 (código E_RETRY)",
    "E_SCHEMA_INVALID": "linha 13, coluna 20: JSON Schema inválido: 'banana' is not valid under any of the given schemas (código E_SCHEMA_INVALID)",
    "E_STEP_ID_DUP": "linha 13, coluna 9: já existe uma etapa com o id `a` (linha 10) (código E_STEP_ID_DUP)",
    "E_STEP_KIND": "linha 10, coluna 5: a etapa precisa ter `agent` (etapa de agente) ou `gate` (etapa de checagem) (código E_STEP_KIND)",
    "E_TOO_LARGE": "o arquivo passa de 256 KB (código E_TOO_LARGE)",
    "E_TYPE": "linha 13, coluna 20: `stall_minutes` deve ser número inteiro (código E_TYPE)",
    "E_UNKNOWN_FIELD": "linha 13, coluna 5: campo desconhecido `retires` (você quis dizer `retries`?) (código E_UNKNOWN_FIELD)",
    "E_VERSION": "linha 1, coluna 1: `version` é obrigatório e precisa ser 1 (código E_VERSION)",
    "E_YAML_FORBIDDEN": "linha 3, coluna 8: âncoras e aliases (&x, *x) não são permitidos em pipelines (código E_YAML_FORBIDDEN)",
    "E_YAML_SYNTAX": "linha 4, coluna 1: YAML inválido: expected the node content, but found '<stream end>' (código E_YAML_SYNTAX)",
}
EXPECTED_WARNINGS: dict[str, str] = {
    "W_BUDGET_DEFAULT": "linha 1, coluna 1: o pipeline não declara `budget`: vale o teto padrão (2.000.000 de tokens, US$ 15 e 120 minutos) (código W_BUDGET_DEFAULT)",
    "W_BUDGET_FANOUT": "linha 39, coluna 5: o teto por agente da etapa `implementar` multiplicado por até 50 itens passa do `budget` do pipeline; o teto do pipeline continua sendo o limite, e itens finais podem não rodar (código W_BUDGET_FANOUT)",
    "W_UNREACHABLE_STEP": "linha 14, coluna 9: a etapa `b` nunca é alcançada (código W_UNREACHABLE_STEP)",
}


def _render(code: str) -> list[str]:
    text, context = CASES[code]
    definition, report = validate_structure(text)
    found = list(report.errors)
    if context is not None and definition is not None:
        found += list(validate_context(definition, context).errors)
    return [item.format() for item in found if item.code == code]


def test_every_error_code_has_a_case() -> None:
    assert set(CASES) == set(ERROR_CODES)


@pytest.mark.parametrize("code", sorted(CASES))
def test_error_message_snapshot(code: str) -> None:
    rendered = _render(code)
    assert rendered, f"{code} was not produced"
    assert rendered[0] == EXPECTED[code]
    assert rendered[0].endswith(f"(código {code})")


@pytest.mark.parametrize("code", sorted(WARNING_CASES))
def test_warning_message_snapshot(code: str) -> None:
    _, report = validate_structure(WARNING_CASES[code])
    rendered = [item.format() for item in report.warnings if item.code == code]
    assert rendered, f"{code} was not produced"
    assert rendered[0] == EXPECTED_WARNINGS[code]


def test_warning_cases_cover_structural_warnings() -> None:
    assert set(WARNING_CASES) == set(WARNING_CODES) - {"W_EFFORT_ADJUSTED"}


def test_messages_with_a_line_use_the_documented_shape() -> None:
    text, _ = CASES["E_UNKNOWN_FIELD"]
    _, report = validate_structure(text)
    assert report.errors[0].format().startswith("linha ")
    assert ", coluna " in report.errors[0].format()
