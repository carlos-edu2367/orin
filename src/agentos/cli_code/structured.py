"""``--json-schema``: the run's answer must be one JSON value that satisfies the schema (W3-R25).

The model is told the schema with the turn's appended instructions. After a turn completes, its final text is parsed
and validated; an invalid answer gets a follow-up message naming the problem, up to ``MAX_STRUCTURED_RETRIES`` times,
after which the run ends with ``error_max_structured_output_retries``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import best_match

from .args import CliUsageError

MAX_STRUCTURED_RETRIES = 3
_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.S)


def load_schema(raw: str) -> dict[str, Any]:
    try:
        schema = json.loads(raw)
    except ValueError as error:
        raise CliUsageError("--json-schema is not valid JSON") from error
    if not isinstance(schema, dict):
        raise CliUsageError("--json-schema must be a JSON object")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise CliUsageError(f"--json-schema is not a valid JSON Schema: {error.message}") from error
    return schema


def schema_instructions(schema: dict[str, Any]) -> str:
    return (
        "Your final answer in this run must be exactly one JSON value, with no other text and no code fence, that "
        "satisfies this JSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
    )


def extract_json(text: str) -> Any:
    """The answer's JSON value: the whole text, else a fenced block, else the outermost object or array."""
    candidate = (text or "").strip()
    try:
        return json.loads(candidate)
    except ValueError:
        pass
    fenced = _FENCE.search(candidate)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except ValueError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = candidate.find(opener), candidate.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(candidate[start:end + 1])
            except ValueError:
                continue
    raise ValueError("the answer has no JSON value")


def problem(schema: dict[str, Any], text: str) -> tuple[Any, str | None]:
    """``(value, None)`` when the text holds a valid value, else ``(None, what is wrong)``."""
    try:
        value = extract_json(text)
    except ValueError as error:
        return None, str(error)
    error = best_match(Draft202012Validator(schema).iter_errors(value))
    if error is None:
        return value, None
    where = "/".join(str(part) for part in error.absolute_path) or "the top level"
    return None, f"at {where}: {error.message}"


def retry_prompt(issue: str) -> str:
    return f"That answer does not satisfy the required JSON Schema ({issue}). Reply again with only the JSON value."


__all__ = ["MAX_STRUCTURED_RETRIES", "extract_json", "load_schema", "problem", "retry_prompt", "schema_instructions"]
