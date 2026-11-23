"""Shared helpers for the pipeline tests."""
from __future__ import annotations

from pathlib import Path
import textwrap

from agentos.pipelines.errors import ValidationReport
from agentos.pipelines.validator import validate_structure

FIXTURES = Path(__file__).parent / "fixtures"

HEADER = """\
version: 1
id: teste
name: Teste
budget: {max_tokens: 100000}
inputs:
  testes: {type: string, default: "pytest -q"}
  arquivo: {type: string}
  modelos: {type: models, default: auto}
"""


def pipeline(steps: str, header: str = HEADER) -> str:
    """A complete document: the default header plus ``steps`` (indented as given, de-dented here)."""
    body = textwrap.indent(textwrap.dedent(steps).strip("\n"), "  ")
    return f"{header}steps:\n{body}\n"


def structure(text: str, source: str = "user"):
    return validate_structure(text, source)


def codes(text: str) -> tuple[str, ...]:
    return validate_structure(text)[1].codes()


def report(text: str) -> ValidationReport:
    return validate_structure(text)[1]


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
