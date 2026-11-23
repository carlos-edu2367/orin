"""`sanitize_assistant_text`: the fixtures shared with the frontend, plus the stream form of the same rule."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.agentic.text_sanitize import new_assistant_text_stream, sanitize_assistant_text

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "text_tool_leak_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def test_the_shared_fixture_covers_the_required_cases() -> None:
    names = " | ".join(case["name"] for case in CASES)
    for needed in ("print", "html-escaped", "code fence", "legitimate result", "[Tool Use]", "Orin log", "without a closing"):
        assert needed in names, needed


@pytest.mark.parametrize("case", CASES, ids=[case["name"][:40] for case in CASES])
def test_sanitize_matches_the_shared_fixture(case: dict[str, str]) -> None:
    assert sanitize_assistant_text(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"][:40] for case in CASES])
@pytest.mark.parametrize("step", [1, 5, 4096])
def test_the_stream_form_agrees_with_the_function(case: dict[str, str], step: int) -> None:
    text = case["input"]
    stream = new_assistant_text_stream()
    shown = "".join(stream.feed(text[i : i + step]) for i in range(0, len(text), step)) + stream.finish()
    assert " ".join(shown.split()) == " ".join(case["expected"].split())


def test_the_print_keeps_both_prose_lines() -> None:
    printed = next(case for case in CASES if case["name"].startswith("print"))
    out = sanitize_assistant_text(printed["input"])
    assert "Por código: os 10 PACOTE_344" in out and "listo os 19 restantes para ler." in out
    assert "invoke" not in out and "function_results" not in out and "</output>" not in out


def test_empty_and_non_text_inputs_are_safe() -> None:
    assert sanitize_assistant_text("") == ""
    assert sanitize_assistant_text(None) == ""  # type: ignore[arg-type]
