from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agentos.pipelines import yaml_loader
from agentos.pipelines.errors import PipelineLoadError
from agentos.pipelines.yaml_loader import load_yaml


def _code(text: str) -> PipelineLoadError:
    with pytest.raises(PipelineLoadError) as info:
        load_yaml(text)
    return info.value


def test_module_never_uses_unrestricted_loaders() -> None:
    tree = ast.parse(Path(yaml_loader.__file__).read_text(encoding="utf-8"))
    forbidden = {"unsafe_load", "full_load", "FullLoader", "UnsafeLoader", "Loader", "unsafe_load_all", "full_load_all"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            raise AssertionError(f"forbidden yaml API: {node.attr} (line {node.lineno})")
        if isinstance(node, ast.Name) and node.id in forbidden:
            raise AssertionError(f"forbidden yaml name: {node.id} (line {node.lineno})")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "load":
            raise AssertionError(f"yaml.load call (line {node.lineno})")


def test_plain_document_roundtrips_with_types() -> None:
    loaded = load_yaml("version: 1\nid: abc\nratio: 1.5\nflag: true\nother: false\nnothing: null\nitems: [1, two]\n")
    assert loaded.data == {
        "version": 1, "id": "abc", "ratio": 1.5, "flag": True, "other": False, "nothing": None, "items": [1, "two"],
    }
    assert loaded.data["flag"] is True


def test_on_key_and_yaml11_booleans_stay_text() -> None:
    loaded = load_yaml("retries: {max: 1, on: [failed]}\na: yes\nb: no\nc: off\nd: on\n")
    assert loaded.data["retries"] == {"max": 1, "on": ["failed"]}
    assert loaded.data["a"] == "yes" and loaded.data["b"] == "no"
    assert loaded.data["c"] == "off" and loaded.data["d"] == "on"


def test_dates_stay_text() -> None:
    assert load_yaml("when: 2026-01-01\n").data == {"when": "2026-01-01"}


def test_positions_are_one_based() -> None:
    loaded = load_yaml("version: 1\nsteps:\n  - id: a\n    agent: plan\n")
    assert loaded.where("version") == (1, 10)
    assert loaded.where("steps[0].agent") == (4, 12)
    assert loaded.key_where("steps[0].agent") == (4, 5)
    assert loaded.where("steps[0].missing")[0] == 3  # nearest known parent


@pytest.mark.parametrize(
    "text",
    [
        "a: &x 1\nb: *x\n",
        "a: &x [1]\n",
        "a: !!python/object/apply:os.system ['echo']\n",
        "a: !custom 1\n",
        "a: 1\na: 2\n",
        "base: {x: 1}\nother:\n  <<: {y: 2}\n",
        "a: 1\n---\nb: 2\n",
        "a: !!binary aGk=\n",
        "a: !!set {x}\n",
    ],
)
def test_forbidden_constructs_are_rejected(text: str) -> None:
    error = _code(text).error
    assert error.code in {"E_YAML_FORBIDDEN", "E_YAML_SYNTAX"}
    assert error.line is not None


def test_alias_is_forbidden_not_expanded() -> None:
    error = _code("a: &x [1, 2]\nb: *x\n").error
    assert error.code == "E_YAML_FORBIDDEN"
    assert error.line == 1


def test_duplicate_key_points_at_second_line() -> None:
    error = _code("a: 1\nb: 2\na: 3\n").error
    assert error.code == "E_YAML_FORBIDDEN"
    assert error.line == 3


def test_multiple_documents_rejected() -> None:
    assert _code("a: 1\n---\nb: 2\n").error.code == "E_YAML_FORBIDDEN"


def test_syntax_error_has_line_and_column() -> None:
    error = _code("a: 1\nb: [1, 2\nc: 3\n").error
    assert error.code == "E_YAML_SYNTAX"
    assert error.line is not None and error.column is not None


def test_too_large_is_rejected_before_parsing() -> None:
    error = _code("a: " + "x" * (256 * 1024 + 1)).error
    assert error.code == "E_TOO_LARGE"


def test_depth_limit() -> None:
    deep = "a: " + "[" * 17 + "]" * 17 + "\n"
    assert _code(deep).error.code == "E_YAML_FORBIDDEN"
    ok = "a: " + "[" * 10 + "]" * 10 + "\n"
    assert load_yaml(ok).data == {"a": [[[[[[[[[[]]]]]]]]]]}


def test_non_text_key_rejected() -> None:
    assert _code("1: a\n").error.code == "E_TYPE"


def test_empty_document_is_syntax_error() -> None:
    assert _code("").error.code == "E_YAML_SYNTAX"
    assert _code("# only a comment\n").error.code == "E_YAML_SYNTAX"


def test_crlf_text_is_accepted() -> None:
    assert load_yaml("a: 1\r\nb: 2\r\n").data == {"a": 1, "b": 2}
