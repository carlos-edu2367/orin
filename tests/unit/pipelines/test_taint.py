from __future__ import annotations

import textwrap

from agentos.pipelines.taint import Origin, TaintScope, classify, over_origin

from .helpers import codes, pipeline


def test_classify_roots() -> None:
    assert classify(("inputs", "x")) is Origin.TRUSTED
    assert classify(("steps", "a", "output")) is Origin.UNTRUSTED
    assert classify(("steps", "a", "findings")) is Origin.UNTRUSTED
    assert classify(("unknown",)) is Origin.UNTRUSTED
    assert classify(()) is Origin.UNTRUSTED


def test_map_variable_inherits_origin_of_over() -> None:
    scope = TaintScope({"t": over_origin(("steps", "a", "output", "l")), "u": over_origin(("inputs", "l")), "v": over_origin(None)})
    assert classify(("t", "x"), scope) is Origin.UNTRUSTED
    assert classify(("u", "x"), scope) is Origin.TRUSTED
    assert classify(("v",), scope) is Origin.TRUSTED


PLAN = """
- id: plano
  agent: plan
  prompt: faça
"""


def _after(extra: str) -> str:
    return pipeline(textwrap.dedent(PLAN) + textwrap.dedent(extra))


def test_step_output_in_gate_command_is_refused() -> None:
    text = _after(
        """
        - id: g
          gate:
            all:
              - command: ${{ steps.plano.output.x }}
        """
    )
    assert "E_REF_TAINT" in codes(text)


def test_step_output_inside_command_text_is_refused() -> None:
    text = _after(
        """
        - id: g
          gate:
            all:
              - command: pytest ${{ steps.plano.output.x }}
        """
    )
    assert "E_REF_TAINT" in codes(text)


def test_input_in_gate_command_is_accepted_both_forms() -> None:
    whole = _after(
        """
        - id: g
          gate:
            all:
              - command: ${{ inputs.testes }}
        """
    )
    inline = _after(
        """
        - id: g
          gate:
            all:
              - command: pytest -q ${{ inputs.arquivo }}
        """
    )
    assert codes(whole) == ()
    assert codes(inline) == ()


def test_taint_in_provider_model_effort_and_agent_is_refused() -> None:
    for field in ("provider", "model", "effort", "agent"):
        base = "" if field == "agent" else "  agent: general\n"
        extra = f"- id: s2\n{base}  prompt: x\n  {field}: ${{{{ steps.plano.output.x }}}}\n"
        assert "E_REF_TAINT" in codes(_after(extra)), field


def test_input_in_model_is_accepted() -> None:
    extra = """
    - id: s2
      agent: general
      model: ${{ inputs.arquivo }}
      prompt: x
    """
    assert codes(_after(extra)) == ()


def test_map_variable_over_steps_refused_in_model_accepted_in_prompt() -> None:
    plan = """
    - id: plano
      agent: plan
      prompt: faça
    - id: s2
      agent: general
      map: {over: "${{ steps.plano.output.itens }}", as: item, max_parallel: 2}
      model: ${{ item.modelo }}
      prompt: x
    """
    assert "E_REF_TAINT" in codes(pipeline(plan))
    ok = plan.replace("      model: ${{ item.modelo }}\n", "").replace("prompt: x", "prompt: use ${{ item.modelo }}")
    assert codes(pipeline(ok)) == ()


def test_map_variable_over_literal_or_inputs_accepted_in_provider_model_effort() -> None:
    literal = """
    - id: s2
      agent: general
      map: {over: [{m: a}, {m: b}], as: item, max_parallel: 2}
      model: ${{ item.m }}
      prompt: x
    """
    from_inputs = """
    - id: s2
      agent: general
      map: {over: "${{ inputs.modelos }}", as: item, max_parallel: 2}
      provider: ${{ item.provider }}
      model: ${{ item.model }}
      effort: ${{ item.effort }}
      prompt: x
    """
    assert codes(pipeline(literal)) == ()
    assert codes(pipeline(from_inputs)) == ()


def test_findings_only_in_prompt() -> None:
    text = pipeline(
        """
        - id: v
          gate:
            all:
              - command: ${{ inputs.testes }}
          on_fail: continue
        - id: c
          agent: general
          prompt: corrija ${{ steps.v.findings }}
        - id: g2
          gate:
            all:
              - command: echo ${{ steps.v.findings }}
        """
    )
    found = codes(text)
    assert found.count("E_REF_TAINT") == 1
