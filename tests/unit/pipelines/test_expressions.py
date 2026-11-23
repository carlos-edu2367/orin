from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agentos.pipelines import expressions
from agentos.pipelines.expressions import (
    ExpressionError,
    collect_paths,
    evaluate,
    parse_expression,
    render_template,
    split_template,
    unwrap_expression,
)

CTX = {
    "inputs": {"x": 3, "name": "ana", "flag": True, "none": None},
    "steps": {
        "plano": {"output": {"campo": "v", "tarefas": [1, 2, 3], "nested": {"k": [1]}}, "failed": False, "findings": []},
        "verificar": {"failed": True, "findings": [{"id": "f1"}]},
    },
    "tarefa": {"titulo": "t1", "arquivos": ["a.py", "b.py"]},
}


def resolve(segments: tuple[str, ...]):
    return expressions.resolve_path(CTX, segments)


def ev(text: str):
    return evaluate(parse_expression(text), resolve)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("inputs.x", 3),
        ("inputs.name", "ana"),
        ("steps.plano.output.campo", "v"),
        ("steps.plano.failed", False),
        ("steps.verificar.failed", True),
        ("steps.verificar.findings", [{"id": "f1"}]),
        ("tarefa.titulo", "t1"),
        ("'abc'", "abc"),
        ('"a b"', "a b"),
        ("12", 12),
        ("-4", -4),
        ("true", True),
        ("false", False),
        ("null", None),
        ("inputs.x == 3", True),
        ("inputs.x != 3", False),
        ("inputs.name == 'ana'", True),
        ("steps.verificar.failed and inputs.flag", True),
        ("steps.plano.failed or inputs.flag", True),
        ("not steps.plano.failed", True),
        ("not not inputs.flag", True),
        ("len(steps.plano.output.tarefas) == 3", True),
        ("len(tarefa.arquivos) != 2", False),
        ("not steps.plano.failed and steps.verificar.failed", True),
        ("true or false and false", True),
        ("(true or false) and false", False),
        ("not (inputs.x == 3)", False),
        ("inputs.missing", None),
        ("steps.nope.failed", None),
        ("inputs.none == null", True),
    ],
)
def test_valid_expressions(text: str, expected: object) -> None:
    assert ev(text) == expected


def test_precedence_not_binds_tighter_than_and_than_or() -> None:
    assert ev("not true or true") is True
    assert ev("not true and true") is False
    assert ev("true or false and false") is True


def test_collect_paths() -> None:
    expr = parse_expression("steps.a.failed and len(inputs.b) == 2 or tarefa.x")
    assert [p.segments for p in collect_paths(expr)] == [("steps", "a", "failed"), ("inputs", "b"), ("tarefa", "x")]


def test_unwrap_expression() -> None:
    assert unwrap_expression("${{ steps.a.failed }}") == "steps.a.failed"
    assert unwrap_expression("  ${{inputs.x}}  ") == "inputs.x"
    assert unwrap_expression("steps.a.failed") == "steps.a.failed"


HOSTILE = [
    "__import__('os')",
    "inputs.__class__",
    "inputs.x.__dict__",
    "inputs._private",
    "a[0]",
    "a = 1",
    'f"x"',
    "lambda: 1",
    "${{ ${{ x }} }}",
    "foo(x)",
    "len(1, 2)",
    "len()",
    "len(inputs.a, inputs.b)",
    "len(1)",
    "len('abc')",
    "x" * 501,
    "a.b.c.d.e.f.g.h.i",
    "a\x00b",
    "a\nb\x07",
    "inputs.x +",
    "inputs.x + 1",
    "inputs.x - 1",
    "inputs.x * 2",
    "inputs.x < 3",
    "inputs.x > 3",
    "inputs.x <= 3",
    "inputs.x >= 3",
    "inputs.x == == 3",
    "(inputs.x",
    "inputs.x)",
    "()",
    "inputs.",
    ".inputs",
    "inputs..x",
    "'unterminated",
    "1.5",
    "0x10",
    "inputs.x if true else false",
    "inputs.x in inputs.y",
    "not",
    "and true",
    "true and",
    "inputs.x ; inputs.y",
    "inputs.x, inputs.y",
    "{}",
    "[1]",
    "inputs.x ? 1 : 2",
    "`id`",
    "$(id)",
    "True",
    "Inputs.x",
    "",
    "   ",
    "'a' 'b'",
    "inputs.x inputs.y",
    "steps.a.output['x']",
    "inputs.x.y.z.w.v.u.t.s",
    "@x",
    "inputs.x == 3 == 3",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_expressions_are_rejected_at_parse(text: str) -> None:
    with pytest.raises(ExpressionError):
        parse_expression(text)


def test_at_least_forty_hostile_cases() -> None:
    assert len(HOSTILE) >= 40


def test_eight_segments_allowed_nine_rejected() -> None:
    parse_expression(".".join(["a"] * 8))
    with pytest.raises(ExpressionError):
        parse_expression(".".join(["a"] * 9))


def test_len_of_non_sized_is_zero_at_runtime() -> None:
    assert ev("len(inputs.none)") == 0
    assert ev("len(inputs.missing)") == 0


def test_template_parts_with_several_snippets() -> None:
    parts = split_template("Faça ${{ inputs.name }} e ${{ steps.plano.output.campo }}: ${{ tarefa.arquivos }}.")
    assert [type(p).__name__ for p in parts] == ["str", "TemplateRef", "str", "TemplateRef", "str", "TemplateRef", "str"]
    out = render_template("Faça ${{ inputs.name }} e ${{ steps.plano.output.campo }}: ${{ tarefa.arquivos }}.", resolve)
    assert out == 'Faça ana e v: ["a.py","b.py"].'


def test_render_scalars_and_composites() -> None:
    assert render_template("${{ inputs.flag }}|${{ inputs.none }}|${{ inputs.x }}", resolve) == "true|null|3"
    assert render_template("${{ steps.plano.output.nested }}", resolve) == '{"k":[1]}'
    assert render_template("sem expressão", resolve) == "sem expressão"
    assert render_template("${{ inputs.missing }}", resolve) == "null"


@pytest.mark.parametrize(
    "text",
    [
        "${{ inputs.x == 3 }}",
        "${{ not inputs.x }}",
        "${{ len(inputs.x) }}",
        "${{ 'lit' }}",
        "${{ inputs.x ",
        "${{ ${{ inputs.x }} }}",
        "${{ }}",
        "${{ inputs.__class__ }}",
    ],
)
def test_templates_accept_only_paths(text: str) -> None:
    with pytest.raises(ExpressionError):
        split_template(text)


def test_template_error_offset_points_inside_text() -> None:
    with pytest.raises(ExpressionError) as info:
        split_template("abc ${{ inputs.x == 3 }} def")
    assert info.value.offset >= 4


def test_module_has_no_dynamic_evaluation() -> None:
    tree = ast.parse(Path(expressions.__file__).read_text(encoding="utf-8"))
    banned = {"eval", "exec", "compile", "__import__", "literal_eval", "attrgetter", "getattr"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in banned:
            raise AssertionError(f"banned name {node.id} at line {node.lineno}")
        if isinstance(node, ast.Attribute) and node.attr in banned:
            raise AssertionError(f"banned attribute {node.attr} at line {node.lineno}")
        if isinstance(node, ast.ImportFrom) and node.module == "operator":
            raise AssertionError("operator import")
