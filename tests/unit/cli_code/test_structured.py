"""W6 Task 11: --json-schema reads, validates and explains structured answers."""
from __future__ import annotations

import pytest

from agentos.cli_code.args import CliUsageError
from agentos.cli_code.structured import extract_json, load_schema, problem, retry_prompt, schema_instructions

SCHEMA = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}


@pytest.mark.parametrize("text", ['{"n": 3}', '  {"n": 3}\n', 'Here:\n```json\n{"n": 3}\n```', 'The value is {"n": 3}.'])
def test_the_json_value_is_found(text: str) -> None:
    assert extract_json(text) == {"n": 3}


def test_validation_names_the_problem() -> None:
    assert problem(SCHEMA, '{"n": 3}') == ({"n": 3}, None)
    value, issue = problem(SCHEMA, '{"n": "three"}')
    assert value is None and issue.startswith("at n:")
    assert problem(SCHEMA, "no json here")[1] == "the answer has no JSON value"
    assert "only the JSON value" in retry_prompt("x") and '"required": ["n"]' in schema_instructions(SCHEMA)


@pytest.mark.parametrize("raw", ["{not json", "[1]", '{"type": 5}'])
def test_a_bad_schema_is_a_usage_error(raw: str) -> None:
    with pytest.raises(CliUsageError):
        load_schema(raw)
