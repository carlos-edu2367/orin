from __future__ import annotations

import json

import pytest

from agentos.pipelines.model import AgentStep, CommandGate, GateStep, VerifierGate
from agentos.pipelines.validator import validate_structure

from .helpers import HEADER, codes, fixture_text, pipeline, report

OK_STEP = """
- id: a
  agent: general
  prompt: faça
"""


def _with_header(header_extra: str, steps: str = OK_STEP) -> str:
    return pipeline(steps, header=HEADER + header_extra)


def test_spec_example_validates_and_builds_the_definition() -> None:
    text = fixture_text("implementar-verificar.yaml")
    definition, result = validate_structure(text, "builtin")
    assert result.errors == ()
    assert definition is not None
    assert definition.id == "implementar-verificar" and definition.source == "builtin"
    assert [s.id for s in definition.steps] == ["plano", "implementar", "verificar", "corrigir"]
    plano, implementar, verificar, corrigir = definition.steps
    assert isinstance(plano, AgentStep) and plano.output_schema is not None and plano.effort == "high"
    assert isinstance(implementar, AgentStep) and implementar.map is not None
    assert implementar.map.var == "tarefa" and implementar.map.max_parallel == 3
    assert implementar.isolation == "worktree" and implementar.budget is not None
    assert implementar.budget.max_tokens == 400000
    assert isinstance(verificar, GateStep)
    first, second = verificar.gate.items
    assert verificar.gate.mode == "all"
    assert isinstance(first, CommandGate) and first.timeout_seconds == 900 and first.expect == "zero"
    assert isinstance(second, VerifierGate) and second.policy == "cross_family" and second.pass_on == ("pass",)
    assert verificar.on_fail.action == "goto" and verificar.on_fail.goto == "corrigir"
    assert verificar.on_fail.max_loops == 2
    assert isinstance(corrigir, AgentStep) and corrigir.next == "verificar"
    assert definition.defaults.retries is not None and definition.defaults.retries.on == ("failed", "stalled")
    assert definition.budget is not None and definition.budget.max_cost_usd == 15
    assert [i.name for i in definition.inputs] == ["objetivo", "testes"]
    assert len(definition.definition_hash) == 64


def test_definition_hash_ignores_line_ending_style() -> None:
    text = fixture_text("implementar-verificar.yaml")
    first = validate_structure(text)[0]
    second = validate_structure(text.replace("\n", "\r\n"))[0]
    assert first is not None and second is not None
    assert first.definition_hash == second.definition_hash


def test_fanout_budget_warning_on_the_spec_example() -> None:
    result = report(fixture_text("implementar-verificar.yaml"))
    assert result.warning_codes() == ("W_BUDGET_FANOUT",)


def test_missing_pipeline_budget_warns() -> None:
    text = pipeline(OK_STEP, header="version: 1\nid: t\nname: T\n")
    result = report(text)
    assert result.errors == ()
    assert "W_BUDGET_DEFAULT" in result.warning_codes()


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("name: T\nsteps: []\n", "E_VERSION"),
        ("version: 2\nid: t\nsteps: []\n", "E_VERSION"),
        ("version: '1'\nid: t\nsteps: []\n", "E_VERSION"),
        ("version: 1\nid: Bad_ID\nsteps: []\n", "E_ID"),
        ("version: 1\nid: " + "a" * 65 + "\nsteps: []\n", "E_ID"),
        ("version: 1\nid: ''\nsteps: []\n", "E_ID"),
    ],
)
def test_version_and_id(header: str, expected: str) -> None:
    assert expected in codes(header + ("steps: []\n" if "steps" not in header else ""))


def test_unknown_key_suggests_the_closest_field() -> None:
    text = pipeline(OK_STEP, header=HEADER + "defaults:\n  retires: {max: 1}\n")
    result = report(text)
    assert result.codes() == ("E_UNKNOWN_FIELD",)
    assert "retries" in result.errors[0].message
    assert "você quis dizer" in result.errors[0].message


def test_unknown_key_without_close_match_has_no_suggestion() -> None:
    text = pipeline(OK_STEP + "  zzzzzzzz: 1\n")
    result = report(text)
    assert result.codes() == ("E_UNKNOWN_FIELD",)
    assert "você quis dizer" not in result.errors[0].message


def test_required_fields() -> None:
    assert "E_REQUIRED" in codes(pipeline("- agent: general\n  prompt: x\n"))
    assert "E_REQUIRED" in codes(pipeline("- id: a\n  agent: general\n"))
    assert "E_REQUIRED" in codes("version: 1\nid: t\n")


def test_types() -> None:
    assert "E_TYPE" in codes("version: 1\nid: t\nsteps: nope\n")
    assert "E_TYPE" in codes("- 1\n- 2\n")
    assert "E_TYPE" in codes(pipeline("- id: a\n  agent: general\n  prompt: x\n  stall_minutes: abc\n"))
    assert "E_TYPE" in codes(pipeline("- id: a\n  agent: general\n  prompt: x\n  isolation: banana\n"))


def test_step_needs_exactly_one_of_agent_or_gate() -> None:
    both = pipeline(
        """
        - id: a
          agent: general
          prompt: x
          gate: {all: [{command: "pytest"}]}
        """
    )
    neither = pipeline("- id: a\n  prompt: x\n")
    assert codes(both) == ("E_STEP_KIND",)
    assert "E_STEP_KIND" in codes(neither)


def test_gate_step_rejects_agent_only_fields() -> None:
    text = pipeline(
        """
        - id: a
          gate: {all: [{command: "pytest"}]}
          prompt: x
        """
    )
    assert codes(text) == ("E_STEP_KIND",)


def test_duplicate_step_ids() -> None:
    assert codes(pipeline(OK_STEP + OK_STEP)) == ("E_STEP_ID_DUP",)


def test_step_id_must_be_a_valid_reference_name() -> None:
    assert "E_ID" in codes(pipeline("- id: Bad-Id\n  agent: general\n  prompt: x\n"))


@pytest.mark.parametrize("parallel", [0, 9, -1])
def test_map_parallel_range(parallel: int) -> None:
    step = f"""
    - id: a
      agent: general
      prompt: x
      map: {{over: [1, 2], as: i, max_parallel: {parallel}}}
    """
    assert "E_MAP_RANGE" in codes(pipeline(step))


def test_map_literal_over_limit() -> None:
    items = ", ".join(str(n) for n in range(51))
    step = f"""
    - id: a
      agent: general
      prompt: x
      map: {{over: [{items}], as: i, max_parallel: 2}}
    """
    assert "E_MAP_RANGE" in codes(pipeline(step))
    fifty = ", ".join(str(n) for n in range(50))
    assert codes(pipeline(step.replace(items, fifty))) == ()


def test_map_variable_name_rules() -> None:
    step = """
    - id: a
      agent: general
      prompt: x
      map: {over: [1], as: inputs}
    """
    assert "E_REF_UNKNOWN" not in codes(pipeline(step))
    assert "E_ID" in codes(pipeline(step))


def test_map_default_parallelism_and_fail_fast() -> None:
    step = """
    - id: a
      agent: general
      prompt: x
      map: {over: [1], as: i}
    """
    definition = validate_structure(pipeline(step))[0]
    assert definition is not None
    assert isinstance(definition.steps[0], AgentStep) and definition.steps[0].map is not None
    assert definition.steps[0].map.max_parallel == 2 and definition.steps[0].map.fail_fast is True


@pytest.mark.parametrize(
    "retries",
    ["{max: 4}", "{max: -1}", "{max: 1, on: [boom]}", "{max: 1, on: failed}"],
)
def test_retry_policy(retries: str) -> None:
    step = f"- id: a\n  agent: general\n  prompt: x\n  retries: {retries}\n"
    assert "E_RETRY" in codes(pipeline(step))


@pytest.mark.parametrize("loops", [0, 6])
def test_on_fail_max_loops_range(loops: int) -> None:
    step = f"""
    - id: a
      agent: general
      prompt: x
    - id: g
      gate: {{all: [{{command: pytest}}]}}
      on_fail: {{goto: a, max_loops: {loops}}}
    """
    assert "E_LOOP_NO_MAX" in codes(pipeline(step))


def test_on_fail_forms() -> None:
    for form, action in (("stop", "stop"), ("continue", "continue")):
        step = f"- id: g\n  gate: {{all: [{{command: pytest}}]}}\n  on_fail: {form}\n"
        definition = validate_structure(pipeline(step))[0]
        assert definition is not None and definition.steps[0].on_fail.action == action
    assert "E_TYPE" in codes(pipeline("- id: g\n  gate: {all: [{command: pytest}]}\n  on_fail: explode\n"))


@pytest.mark.parametrize("budget", ["{max_tokens: -5}", "{max_tokens: abc}", "{max_cost_usd: -1}", "{max_minutes: true}", "{max_tokens: 1.5}"])
def test_budget_values(budget: str) -> None:
    text = pipeline(OK_STEP, header=f"version: 1\nid: t\nname: T\nbudget: {budget}\n")
    assert "E_BUDGET" in codes(text)
    step = f"- id: a\n  agent: general\n  prompt: x\n  budget: {budget}\n"
    assert "E_BUDGET" in codes(pipeline(step))


def test_budget_unknown_key() -> None:
    assert "E_UNKNOWN_FIELD" in codes(pipeline(OK_STEP, header="version: 1\nid: t\nname: T\nbudget: {max_token: 5}\n"))


def test_fanout_warning_literal_items_times_step_budget() -> None:
    step = """
    - id: a
      agent: general
      prompt: x
      map: {over: [1, 2, 3], as: i}
      budget: {max_tokens: 40000}
    """
    over = pipeline(step, header="version: 1\nid: t\nname: T\nbudget: {max_tokens: 100000}\n")
    under = pipeline(step, header="version: 1\nid: t\nname: T\nbudget: {max_tokens: 120000}\n")
    assert report(over).warning_codes() == ("W_BUDGET_FANOUT",)
    assert report(over).errors == ()
    assert report(under).warning_codes() == ()


def test_output_schema_rules() -> None:
    def step(schema: str) -> str:
        return f"- id: a\n  agent: general\n  prompt: x\n  output_schema: {schema}\n"

    assert codes(pipeline(step("{type: object, properties: {x: {type: string}}}"))) == ()
    assert "E_SCHEMA_INVALID" in codes(pipeline(step("{type: banana}")))
    assert "E_SCHEMA_INVALID" in codes(pipeline(step("{properties: {x: {$ref: 'http://evil.example/s.json'}}}")))
    assert "E_SCHEMA_INVALID" in codes(pipeline(step("{properties: {x: {$ref: 'file:///etc/passwd'}}}")))
    assert codes(pipeline(step("{properties: {x: {$ref: '#/$defs/y'}}, $defs: {y: {type: string}}}"))) == ()
    big = json.dumps({"type": "object", "description": "x" * (17 * 1024)})
    assert "E_SCHEMA_INVALID" in codes(pipeline(step(big)))


def test_schema_gate_shape_and_json_schema_checks() -> None:
    plan = "- id: p\n  agent: general\n  prompt: x\n"
    ok = plan + "- id: g\n  gate: {all: [{schema: {step: p, json_schema: {type: object}}}]}\n"
    bad = plan + "- id: g\n  gate: {all: [{schema: {step: p, json_schema: {type: banana}}}]}\n"
    assert codes(pipeline(ok)) == ()
    assert "E_SCHEMA_INVALID" in codes(pipeline(bad))
    assert "E_REQUIRED" in codes(pipeline(plan + "- id: g\n  gate: {all: [{schema: {step: p}}]}\n"))


def test_gate_shape() -> None:
    assert "E_TYPE" in codes(pipeline("- id: g\n  gate: {all: [], any: []}\n"))
    assert "E_TYPE" in codes(pipeline("- id: g\n  gate: {}\n"))
    assert "E_TYPE" in codes(pipeline("- id: g\n  gate: {all: [{command: pytest, verifier: {}}]}\n"))
    assert "E_TYPE" in codes(pipeline("- id: g\n  gate: {all: [{}]}\n"))
    assert codes(pipeline("- id: g\n  gate: {any: [{command: pytest}]}\n")) == ()


def test_command_gate_fields() -> None:
    assert "E_TYPE" in codes(pipeline("- id: g\n  gate: {all: [{command: pytest, expect: maybe}]}\n"))
    assert "E_TYPE" in codes(pipeline("- id: g\n  gate: {all: [{command: pytest, timeout_seconds: 0}]}\n"))
    assert "E_TYPE" in codes(pipeline("- id: g\n  gate: {all: [{command: pytest, timeout_seconds: 3601}]}\n"))
    definition = validate_structure(pipeline("- id: g\n  gate: {all: [{command: pytest, expect: nonzero}]}\n"))[0]
    assert definition is not None and isinstance(definition.steps[0], GateStep)
    item = definition.steps[0].gate.items[0]
    assert isinstance(item, CommandGate) and item.expect == "nonzero" and item.timeout_seconds == 600


def test_verifier_gate_fields() -> None:
    assert "E_TYPE" in codes(pipeline("- id: g\n  gate: {all: [{verifier: {policy: banana}}]}\n"))
    assert "E_TYPE" in codes(pipeline("- id: g\n  gate: {all: [{verifier: {pass_on: [maybe]}}]}\n"))
    assert codes(pipeline("- id: g\n  gate: {all: [{verifier: {policy: explicit, provider: anthropic, model: claude-sonnet-5-5}}]}\n")) == ()


@pytest.mark.parametrize(
    "inputs",
    [
        "{x: {type: banana}}",
        "{x: {type: integer, default: abc}}",
        "{x: {type: boolean, default: 1}}",
        "{x: {type: string, default: 5}}",
        "{x: {type: string, max_length: 0}}",
        "{x: {type: string, max_length: 200000}}",
        "{x: {type: models, default: [a, b, c, d]}}",
        "{x: {type: models, default: 7}}",
        "{Bad: {type: string}}",
        "{x: {type: string, required: maybe}}",
    ],
)
def test_input_rules(inputs: str) -> None:
    text = pipeline(OK_STEP, header=f"version: 1\nid: t\nname: T\nbudget: {{max_tokens: 10}}\ninputs: {inputs}\n")
    assert "E_INPUT" in codes(text) or "E_TYPE" in codes(text)
    assert codes(text) != ()


def test_valid_input_kinds() -> None:
    inputs = "{a: {type: integer, default: 3}, b: {type: boolean, default: false}, c: {type: models, default: [anthropic/claude-sonnet-5-5]}, d: {type: models, default: auto}}"
    text = pipeline(OK_STEP, header=f"version: 1\nid: t\nname: T\nbudget: {{max_tokens: 10}}\ninputs: {inputs}\n")
    assert codes(text) == ()


def test_when_expression_syntax_error() -> None:
    step = "- id: a\n  agent: general\n  prompt: x\n  when: ${{ inputs.arquivo + 1 }}\n"
    assert codes(pipeline(step)) == ("E_EXPR_SYNTAX",)
    ok = "- id: a\n  agent: general\n  prompt: x\n  when: ${{ inputs.arquivo == 'x' or not inputs.testes }}\n"
    assert codes(pipeline(ok)) == ()


def test_prompt_template_syntax_error() -> None:
    step = "- id: a\n  agent: general\n  prompt: 'veja ${{ inputs.testes == 1 }}'\n"
    assert codes(pipeline(step)) == ("E_EXPR_SYNTAX",)


def test_yaml_level_errors_surface_through_validate_structure() -> None:
    definition, result = validate_structure("version: 1\nid: t\nsteps: [\n")
    assert definition is None and result.codes() == ("E_YAML_SYNTAX",)
    definition, result = validate_structure("a: &x 1\nb: *x\n")
    assert definition is None and result.codes() == ("E_YAML_FORBIDDEN",)
