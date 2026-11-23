from __future__ import annotations

from .helpers import codes, fixture_text, pipeline, report


def _agent(step_id: str, extra: str = "", prompt: str = "faça") -> str:
    return f"""
- id: {step_id}
  agent: general
  prompt: {prompt}
{extra}"""


def _indent(extra: str) -> str:
    return "".join("  " + line + "\n" for line in extra.strip("\n").splitlines()) if extra.strip() else ""


def test_spec_loop_is_accepted() -> None:
    result = report(fixture_text("implementar-verificar.yaml"))
    assert result.codes() == ()


def test_reference_to_unknown_step_lists_existing_ones() -> None:
    text = pipeline(_agent("a") + _agent("b", prompt="use ${{ steps.nada.output }}"))
    result = report(text)
    assert result.codes() == ("E_REF_UNKNOWN",)
    assert "a" in result.errors[0].message and "b" in result.errors[0].message
    assert result.errors[0].line is not None


def test_reference_to_later_step_is_forward() -> None:
    text = pipeline(_agent("a", prompt="use ${{ steps.b.output }}") + _agent("b"))
    assert codes(text) == ("E_REF_FORWARD",)


def test_self_reference_is_forward() -> None:
    assert codes(pipeline(_agent("a", prompt="use ${{ steps.a.output }}"))) == ("E_REF_FORWARD",)


def test_unknown_input_and_unknown_root() -> None:
    assert codes(pipeline(_agent("a", prompt="${{ inputs.zzz }}"))) == ("E_REF_UNKNOWN",)
    assert codes(pipeline(_agent("a", prompt="${{ banana.x }}"))) == ("E_REF_UNKNOWN",)
    assert codes(pipeline(_agent("a", prompt="${{ steps.a }}"))) == ("E_REF_UNKNOWN",)


def test_unknown_step_field() -> None:
    text = pipeline(_agent("a") + _agent("b", prompt="${{ steps.a.state }}"))
    assert codes(text) == ("E_REF_UNKNOWN",)


def test_backward_next_without_bounded_loop_is_refused() -> None:
    text = pipeline(_agent("a") + _agent("b", extra="  next: a"))
    result = report(text)
    assert result.codes() == ("E_LOOP_NO_MAX",)
    # points at the line of the back edge (`next: a` of step b)
    assert text.splitlines()[result.errors[0].line - 1].strip() == "next: a"


def test_goto_without_max_loops_is_refused() -> None:
    text = pipeline(
        _agent("a")
        + """
- id: g
  gate:
    all:
      - command: ${{ inputs.testes }}
  on_fail: {goto: a}
"""
    )
    assert "E_LOOP_NO_MAX" in codes(text)


def test_goto_to_unknown_step_is_refused() -> None:
    text = pipeline(
        _agent("a")
        + """
- id: g
  gate:
    all:
      - command: ${{ inputs.testes }}
  on_fail: {goto: nada, max_loops: 2}
"""
    )
    assert codes(text) == ("E_REF_UNKNOWN",)


def test_next_to_unknown_step_is_refused() -> None:
    assert codes(pipeline(_agent("a", extra="  next: nada"))) == ("E_REF_UNKNOWN",)


def test_bounded_goto_loop_with_forward_next_is_accepted() -> None:
    text = pipeline(
        _agent("a")
        + """
- id: g
  gate:
    all:
      - command: ${{ inputs.testes }}
  on_fail: {goto: a, max_loops: 3}
"""
    )
    assert codes(text) == ()


def test_unreachable_step_is_a_warning_not_an_error() -> None:
    text = pipeline(_agent("a", extra="  next: c") + _agent("b") + _agent("c"))
    result = report(text)
    assert result.errors == ()
    assert result.warning_codes() == ("W_UNREACHABLE_STEP",)
    assert "b" in result.warnings[0].message
