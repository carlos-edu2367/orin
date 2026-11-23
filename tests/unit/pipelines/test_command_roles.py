"""C10 security review (23/11): one classifier for the position of each reference in a gate command (P1, S2) and the
input checks P4 and P5. The renderer, the approval review and the agent rule must agree on every case."""

from __future__ import annotations

import pytest

from agentos.pipelines.approvals import ApprovalStore
from agentos.pipelines.command_quoting import ROLE_ARGUMENT, ROLE_PROGRAM, ROLE_WHOLE, classify_references
from agentos.pipelines.expressions import split_template
from agentos.pipelines.gates import render_command
from agentos.pipelines.inputs import InputError, program_inputs, resolve_inputs, whole_command_inputs
from agentos.pipelines.validator import validate_structure


def roles(command: str) -> list[str]:
    return [item.role for item in classify_references(split_template(command))]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("${{ inputs.x }}", [ROLE_WHOLE]),
        ("  ${{ inputs.x }}  ", [ROLE_WHOLE]),
        ("\n${{ inputs.x }}\n", [ROLE_WHOLE]),
        ("${{ inputs.x }} --flag", [ROLE_PROGRAM]),
        ("./${{ inputs.x }} -q", [ROLE_PROGRAM]),
        ("FOO=1 ${{ inputs.x }}", [ROLE_PROGRAM]),
        ("pytest -q && ${{ inputs.x }} a", [ROLE_PROGRAM]),
        ("pytest | ${{ inputs.x }}", [ROLE_PROGRAM]),
        ("env ${{ inputs.x }}", [ROLE_PROGRAM]),
        ("sudo -n ${{ inputs.x }}", [ROLE_PROGRAM]),
        ("bash -c ${{ inputs.x }}", [ROLE_PROGRAM]),
        ("pytest -q ${{ inputs.x }}", [ROLE_ARGUMENT]),
        ("pytest > ${{ inputs.x }}", [ROLE_ARGUMENT]),
        ("pytest 2>&1 ${{ inputs.x }}", [ROLE_ARGUMENT]),
        ("${{ inputs.p }} ${{ inputs.a }}", [ROLE_PROGRAM, ROLE_ARGUMENT]),
    ],
)
def test_each_reference_gets_one_role(command: str, expected: list[str]) -> None:
    assert roles(command) == expected


def definition_with(command_yaml: str):  # noqa: ANN201
    text = (
        "version: 1\nid: cmd\nname: N\nbudget: {max_tokens: 1000}\n"
        "inputs:\n  testes: {type: string, default: pytest -q}\n  prog: {type: string, default: pytest}\n  arquivo: {type: string, default: a.py}\n"
        "steps:\n  - id: g\n    gate:\n      all:\n        - command: " + command_yaml
    )
    definition, report = validate_structure(text, "user")
    assert definition is not None, report.errors
    return definition


def test_p1_a_block_scalar_whole_command_is_flagged_and_refused_to_the_agent() -> None:
    definition = definition_with("|\n            ${{ inputs.testes }}\n")
    assert definition.steps[0].gate.items[0].command == "${{ inputs.testes }}"  # the edges are stripped on load
    (item,) = ApprovalStore.commands_for_review(definition).commands
    assert item.whole_value is True and item.program_inputs == ("testes",)
    assert whole_command_inputs(definition) == frozenset({"testes"})
    with pytest.raises(InputError) as caught:
        resolve_inputs(definition, {"testes": "python -c 'print(1)'"}, from_agent=True)
    assert caught.value.code == "PIPELINE_INPUT_NOT_ALLOWED"


def test_p1_the_renderer_and_the_review_agree_on_a_command_with_spaces_around() -> None:
    template = "  ${{ inputs.testes }}  "
    (role,) = classify_references(split_template(template))
    rendered = render_command(template, {"testes": "pytest -q"})
    assert role.role == ROLE_WHOLE and rendered.quoted_values == ("pytest", "-q")


def test_s2_an_input_in_the_first_word_selects_the_program() -> None:
    definition = definition_with("${{ inputs.prog }} --flag ${{ inputs.arquivo }}\n")
    (item,) = ApprovalStore.commands_for_review(definition).commands
    assert item.whole_value is False and item.program_inputs == ("prog",)
    assert program_inputs(definition) == frozenset({"prog"})
    review = ApprovalStore.commands_for_review(definition).notice
    assert "ATENÇÃO" in review and "prog" in review
    with pytest.raises(InputError) as caught:
        resolve_inputs(definition, {"prog": "rm"}, from_agent=True)
    assert caught.value.code == "PIPELINE_INPUT_NOT_ALLOWED"
    resolved = resolve_inputs(definition, {"arquivo": "b.py"}, from_agent=True)  # an argument is still the agent's to choose
    assert resolved.values == {"testes": "pytest -q", "prog": "pytest", "arquivo": "b.py"}
    assert resolve_inputs(definition, {"prog": "ruff"}).values["prog"] == "ruff"  # the person may


MODELS_DEFAULT = (
    "version: 1\nid: custom-models\nname: M\nbudget: {max_tokens: 1000}\n"
    "inputs:\n  revisores:\n    type: models\n    default: ['unconfigured-prov/non-fav-opus']\n  n: {type: integer, default: 1}\n"
    "steps:\n  - id: r\n    agent: general\n    map:\n      over: ${{ inputs.revisores }}\n      as: rev\n"
    "    provider: ${{ rev.provider }}\n    model: ${{ rev.model }}\n    prompt: olhe\n"
)


def test_p4_the_default_of_a_models_input_is_checked_against_the_favorites() -> None:
    definition, report = validate_structure(MODELS_DEFAULT, "project")
    assert definition is not None, report.errors
    with pytest.raises(InputError) as caught:
        resolve_inputs(definition, {}, favorites={"anthropic": ("claude-haiku-5-5",)})
    assert caught.value.code == "PIPELINE_INPUT_INVALID" and "padrão" in caught.value.message


@pytest.mark.parametrize("value", [10**15, -(10**15), 10**40])
def test_p5_a_json_integer_has_the_same_15_digit_limit(value: int) -> None:
    definition, _ = validate_structure(MODELS_DEFAULT.replace("unconfigured-prov/non-fav-opus", "anthropic/claude-haiku-5-5"), "user")
    assert definition is not None
    favorites = {"anthropic": ("claude-haiku-5-5",)}
    assert resolve_inputs(definition, {"n": 999_999_999_999_999}, favorites=favorites).values["n"] == 999_999_999_999_999
    with pytest.raises(InputError) as caught:
        resolve_inputs(definition, {"n": value}, favorites=favorites)
    assert caught.value.code == "PIPELINE_INPUT_INVALID"
