"""WS-C4: JSON extraction, schema validation and the correction loop (pure, no session)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from agentos.agentic.structured_output import (
    MAX_CORRECTIONS,
    ExtractError,
    SchemaError,
    canonical_json,
    correction_message,
    extract_json,
    output_instruction,
    run_with_output_schema,
    validate_instance,
    validate_output_schema,
)

SCHEMA = {
    "type": "object",
    "required": ["tarefas"],
    "properties": {
        "tarefas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "arquivos"],
                "properties": {"id": {"type": "string"}, "arquivos": {"type": "array", "items": {"type": "string"}}},
            },
        }
    },
}


# -- extraction ---------------------------------------------------------------------------------------------

def test_extract_from_fenced_json_block() -> None:
    text = 'Pronto.\n\n```json\n{"a": 1}\n```\n'
    assert extract_json(text) == {"a": 1}


def test_extract_uses_the_last_of_several_blocks() -> None:
    text = '```json\n{"a": 1}\n```\ntexto\n```json\n{"a": 2}\n```'
    assert extract_json(text) == {"a": 2}


def test_extract_whole_text_is_json() -> None:
    assert extract_json('  {"a": [1, 2]}\n') == {"a": [1, 2]}
    assert extract_json("[1, 2, 3]") == [1, 2, 3]


def test_extract_last_balanced_value_at_the_end() -> None:
    text = 'Resultado: veja {nao json} e depois {"a": {"b": [1, {"c": 2}]}}'
    assert extract_json(text) == {"a": {"b": [1, {"c": 2}]}}
    assert extract_json('lista final: ["x", "y"]  ') == ["x", "y"]


def test_extract_ignores_braces_inside_strings() -> None:
    assert extract_json('ok {"a": "} ] {"}') == {"a": "} ] {"}


def test_extract_unfenced_json_block_without_language() -> None:
    assert extract_json('```\n{"a": 3}\n```') == {"a": 3}


def test_extract_text_without_json_is_no_json() -> None:
    with pytest.raises(ExtractError) as excinfo:
        extract_json("Terminei a tarefa, sem dados.")
    assert excinfo.value.code == "no_json"
    with pytest.raises(ExtractError):
        extract_json("")


def test_extract_broken_last_block_is_reported_not_silently_replaced_by_an_earlier_one() -> None:
    text = '```json\n{"a": 1}\n```\n```json\n{"a": }\n```'
    with pytest.raises(ExtractError) as excinfo:
        extract_json(text)
    assert excinfo.value.code == "invalid_json"


def test_extract_refuses_huge_output_without_parsing_it() -> None:
    huge = '{"a": "' + "x" * (1024 * 1024) + '"}'
    with pytest.raises(ExtractError) as excinfo:
        extract_json(huge)
    assert excinfo.value.code == "too_large"


def test_extract_many_braces_is_bounded() -> None:
    text = "{" * 50_000 + " fim"
    with pytest.raises(ExtractError):
        extract_json(text)


# -- schema checks ------------------------------------------------------------------------------------------

def test_validate_output_schema_accepts_a_valid_schema_and_returns_a_dict() -> None:
    assert validate_output_schema(SCHEMA) == SCHEMA


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "banana"},
        {"type": "object", "properties": 3},
        [],
        "x",
        None,
        {"$ref": "http://example.com/schema.json"},
        {"properties": {"a": {"$ref": "https://example.com/x"}}},
        {"properties": {"a": {"$ref": "other.json#/x"}}},
        {"type": "string", "pattern": "x" * 20_000},
    ],
)
def test_validate_output_schema_refuses_invalid_remote_or_oversized(bad: object) -> None:
    with pytest.raises(SchemaError):
        validate_output_schema(bad)


def test_validate_output_schema_size_limit_is_16_kb() -> None:
    ok = {"type": "string", "description": "x" * 15_000}
    assert validate_output_schema(ok) == ok
    with pytest.raises(SchemaError):
        validate_output_schema({"type": "string", "description": "x" * 17_000})


def test_local_ref_resolves() -> None:
    schema = {
        "$defs": {"item": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}}},
        "type": "array",
        "items": {"$ref": "#/$defs/item"},
    }
    validate_output_schema(schema)
    assert validate_instance(schema, [{"id": 1}, {"id": 2}]) == []
    errors = validate_instance(schema, [{"id": "x"}])
    assert errors and errors[0].startswith("$[0].id")


def test_validate_instance_lists_paths_and_caps_errors_at_ten() -> None:
    value = {"tarefas": [{"id": 1, "arquivos": "a"}]}
    errors = validate_instance(SCHEMA, value)
    assert any(error.startswith("$.tarefas[0].id") for error in errors)
    assert any(error.startswith("$.tarefas[0].arquivos") for error in errors)
    many = {"type": "array", "items": {"type": "string"}}
    errors = validate_instance(many, list(range(40)))
    assert len(errors) == 11 and errors[-1].startswith("... ")


def test_correction_message_lists_errors_and_is_truncated_to_2_kb() -> None:
    message = correction_message(["$.a: boom", "$.b: bam"], code="schema")
    assert "$.a: boom" in message and "$.b: bam" in message and "json" in message.lower()
    long_message = correction_message(["$.a: " + "x" * 5000], code="schema")
    assert len(long_message.encode("utf-8")) <= 2048
    no_json = correction_message([], code="no_json")
    assert "json" in no_json.lower()


def test_output_instruction_embeds_the_minified_schema() -> None:
    text = output_instruction({"type": "object", "properties": {"a": {"type": "string"}}})
    assert "End your final message with exactly one JSON value" in text
    assert '{"type":"object","properties":{"a":{"type":"string"}}}' in text


def test_canonical_json_is_compact_sorted_and_keeps_unicode() -> None:
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'


# -- the correction loop --------------------------------------------------------------------------------------

@dataclass
class _Outcome:
    text: str
    ok: bool = True


def _loop(answers: list[str], schema: dict | None = None, **kwargs):
    calls: list[str] = []
    queue = list(answers)

    def run_once() -> _Outcome:
        calls.append("run")
        return _Outcome(queue.pop(0))

    def resume_once(message: str) -> _Outcome:
        calls.append(message)
        return _Outcome(queue.pop(0))

    result = run_with_output_schema(
        run_once, resume_once, schema or SCHEMA, answer_of=lambda o: o.text, succeeded=lambda o: o.ok, **kwargs
    )
    return result, calls


GOOD = json.dumps({"tarefas": [{"id": "t1", "arquivos": ["a.py"]}]})
BAD = json.dumps({"tarefas": [{"id": 1}]})


def test_loop_first_try_success_runs_once() -> None:
    result, calls = _loop([f"```json\n{GOOD}\n```"])
    assert result.ok and result.attempts == 1 and calls == ["run"]
    assert result.value == json.loads(GOOD)
    assert result.canonical == canonical_json(json.loads(GOOD))
    assert result.error_code is None


def test_loop_succeeds_on_the_third_attempt_and_feeds_errors_back() -> None:
    result, calls = _loop(["sem json", BAD, GOOD])
    assert result.ok and result.attempts == 3
    assert calls[0] == "run" and len(calls) == 3
    assert "json" in calls[1].lower()
    assert "$.tarefas[0].arquivos" in calls[2]


def test_loop_fails_on_the_sixth_attempt_with_schema_invalid() -> None:
    result, calls = _loop([BAD] * 6)
    assert not result.ok and result.error_code == "SCHEMA_INVALID"
    assert result.attempts == 1 + MAX_CORRECTIONS == 6
    assert len(calls) == 6
    assert result.errors and result.last_text == BAD


def test_loop_sixth_attempt_can_still_be_valid_only_if_within_five_corrections() -> None:
    result, _ = _loop([BAD] * 5 + [GOOD])
    assert result.ok and result.attempts == 6


def test_loop_custom_correction_limit() -> None:
    result, calls = _loop([BAD] * 3, max_corrections=1)
    assert not result.ok and result.attempts == 2 and len(calls) == 2


def test_loop_stops_when_the_child_itself_failed() -> None:
    calls: list[str] = []

    def run_once() -> _Outcome:
        calls.append("run")
        return _Outcome("budget", ok=False)

    result = run_with_output_schema(
        run_once, lambda m: pytest.fail("must not resume"), SCHEMA, answer_of=lambda o: o.text, succeeded=lambda o: o.ok
    )
    assert not result.ok and result.error_code is None and result.attempts == 1 and calls == ["run"]
    assert result.outcome.text == "budget"


def test_loop_stops_when_a_correction_run_fails() -> None:
    answers = iter([_Outcome(BAD), _Outcome("cancelled", ok=False)])
    result = run_with_output_schema(
        lambda: next(answers),
        lambda m: next(answers),
        SCHEMA,
        answer_of=lambda o: o.text,
        succeeded=lambda o: o.ok,
    )
    assert not result.ok and result.error_code is None and result.attempts == 2
    assert result.outcome.text == "cancelled"


def test_loop_oversized_answer_counts_as_a_failed_attempt() -> None:
    huge = '{"tarefas": "' + "x" * (1024 * 1024) + '"}'
    result, calls = _loop([huge, GOOD])
    assert result.ok and result.attempts == 2
    assert "256" in calls[1]
