"""Structured output for a subagent: pull one JSON value out of its final message, validate it against a
JSON Schema and, when it does not fit, ask the same agent to correct it (at most ``MAX_CORRECTIONS`` times).

Pure functions plus a loop over two callables: nothing here knows about sessions, providers or CLIs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

MAX_SCHEMA_BYTES = 16 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_CORRECTIONS = 5
MAX_ERRORS_LISTED = 10
MAX_CORRECTION_BYTES = 2048
SCHEMA_INVALID = "SCHEMA_INVALID"
_MAX_START_ATTEMPTS = 200
_MAX_SCHEMA_DEPTH = 64
_MAX_ERROR_CHARS = 300
_MAX_LAST_TEXT_CHARS = 8000

_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)
_REQUIRED = re.compile(r"^'(.*)' is a required property$", re.DOTALL)


class SchemaError(ValueError):
    """The ``output_schema`` argument is unusable (not a schema, too big, or it points outside itself)."""


class ExtractError(ValueError):
    """The final message holds no usable JSON. ``code`` is ``no_json``, ``invalid_json`` or ``too_large``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _walk_refs(node: Any, depth: int = 0) -> None:
    if depth > _MAX_SCHEMA_DEPTH:
        raise SchemaError("output_schema is nested too deeply.")
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key == "$ref" and not (isinstance(value, str) and value.startswith("#")):
                raise SchemaError("output_schema may only use local $ref values such as '#/$defs/name'.")
            _walk_refs(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _walk_refs(item, depth + 1)


def validate_output_schema(raw: object) -> dict[str, Any]:
    """Check the public ``output_schema`` argument before any child is started."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError as JsonSchemaError

    if not isinstance(raw, Mapping) or not raw:
        raise SchemaError("output_schema must be a non-empty JSON Schema object.")
    try:
        encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise SchemaError(f"output_schema must be plain JSON: {exc}") from exc
    if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
        raise SchemaError(f"output_schema is larger than {MAX_SCHEMA_BYTES // 1024} KB.")
    schema = dict(raw)
    _walk_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except JsonSchemaError as exc:
        raise SchemaError(f"output_schema is not a valid JSON Schema: {exc.message}") from exc
    return schema


def extract_json(text: str) -> Any:
    """The JSON value a final message ends with: the last fenced json block, else the whole text, else the
    last balanced ``{...}``/``[...]`` that reaches the end of the text."""
    text = str(text or "")
    if len(text.encode("utf-8", errors="ignore")) > MAX_OUTPUT_BYTES:
        raise ExtractError("too_large", f"The answer is larger than {MAX_OUTPUT_BYTES // 1024} KB.")
    blocks = [(lang.lower(), body.strip()) for lang, body in _FENCE.findall(text)]
    tagged = [body for lang, body in blocks if lang == "json"]
    untagged = [body for lang, body in blocks if not lang and body[:1] in {"{", "["}]
    chosen = (tagged or untagged or [None])[-1]
    if chosen is not None:
        try:
            return json.loads(chosen)
        except ValueError as exc:
            raise ExtractError("invalid_json", f"The JSON block could not be parsed: {exc}") from exc
    stripped = text.strip()
    if not stripped:
        raise ExtractError("no_json", "The answer is empty.")
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    end = len(stripped)
    attempts = 0
    for index in range(end - 1, -1, -1):
        if stripped[index] not in "{[":
            continue
        attempts += 1
        if attempts > _MAX_START_ATTEMPTS:
            break
        try:
            value, stop = decoder.raw_decode(stripped, index)
        except ValueError:
            continue
        if stop == end:
            return value
    raise ExtractError("no_json", "The answer does not end with a JSON value.")


def _json_path(parts: Any) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def validate_instance(schema: Mapping[str, Any], value: Any) -> list[str]:
    """Human-readable validation errors (at most ``MAX_ERRORS_LISTED``, then a count of the rest)."""
    from jsonschema import Draft202012Validator

    found = sorted(
        Draft202012Validator(dict(schema)).iter_errors(value),
        key=lambda error: ([str(part) for part in error.absolute_path], error.message),
    )
    lines: list[str] = []
    for error in found:
        path = _json_path(error.absolute_path)
        missing = _REQUIRED.match(error.message) if error.validator == "required" else None
        line = f"{path}.{missing.group(1)}: is required" if missing else f"{path}: {error.message}"
        lines.append(line[:_MAX_ERROR_CHARS])
    if len(lines) > MAX_ERRORS_LISTED:
        rest = len(lines) - MAX_ERRORS_LISTED
        lines = [*lines[:MAX_ERRORS_LISTED], f"... {rest} more error(s)"]
    return lines


def _truncate_bytes(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[: limit - 3].decode("utf-8", errors="ignore") + "..."


def correction_message(errors: list[str], *, code: str) -> str:
    """What the same agent is told when its JSON is missing or does not fit (at most 2 KB)."""
    tail = "Reply with the full corrected JSON in a fenced json block and no text after it."
    if code == "no_json":
        head = "Your last message did not end with a JSON value."
    elif code == "too_large":
        head = f"Your last message was larger than {MAX_OUTPUT_BYTES // 1024} KB; return a much smaller JSON."
    elif code == "invalid_json":
        head = "The JSON in your last message could not be parsed: " + "; ".join(errors)
    else:
        head = "Your JSON does not validate against the schema. Fix these errors:\n" + "\n".join(f"- {e}" for e in errors)
    return _truncate_bytes(f"{head}\n{tail}", MAX_CORRECTION_BYTES)


def output_instruction(schema: Mapping[str, Any]) -> str:
    """The sentence (in English, like the other subagent prompts) appended to the child's task."""
    minified = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return (
        "End your final message with exactly one JSON value that validates against this JSON Schema, "
        f"in a fenced json block. No text after it.\nJSON Schema: {minified}"
    )


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class StructuredRun:
    """Result of the loop. ``ok`` means ``value`` validated; ``error_code`` is ``SCHEMA_INVALID`` only when the
    corrections ran out (a child that failed on its own leaves it None and ``outcome`` says why)."""

    outcome: Any
    ok: bool
    value: Any
    canonical: str
    attempts: int
    error_code: str | None
    errors: tuple[str, ...]
    last_text: str


def _check(schema: Mapping[str, Any], text: str) -> tuple[Any, list[str], str | None]:
    try:
        value = extract_json(text)
    except ExtractError as exc:
        return None, [exc.message], exc.code
    errors = validate_instance(schema, value)
    return (None, errors, "schema") if errors else (value, [], None)


def run_with_output_schema(
    run_once: Callable[[], Any],
    resume_once: Callable[[str], Any],
    schema: Mapping[str, Any],
    *,
    answer_of: Callable[[Any], str],
    succeeded: Callable[[Any], bool],
    max_corrections: int = MAX_CORRECTIONS,
) -> StructuredRun:
    """Run the child, validate its final answer, and resume the same child with the errors until it fits."""
    outcome = run_once()
    attempts = 1
    errors: list[str] = []
    text = ""
    while True:
        if not succeeded(outcome):
            return StructuredRun(outcome, False, None, "", attempts, None, tuple(errors), text[:_MAX_LAST_TEXT_CHARS])
        text = str(answer_of(outcome) or "")
        value, errors, code = _check(schema, text)
        if code is None:
            return StructuredRun(outcome, True, value, canonical_json(value), attempts, None, (), text)
        if attempts > max_corrections:
            return StructuredRun(
                outcome, False, None, "", attempts, SCHEMA_INVALID, tuple(errors), text[:_MAX_LAST_TEXT_CHARS]
            )
        outcome = resume_once(correction_message(errors, code=code))
        attempts += 1


__all__ = [
    "MAX_CORRECTIONS",
    "MAX_OUTPUT_BYTES",
    "MAX_SCHEMA_BYTES",
    "SCHEMA_INVALID",
    "ExtractError",
    "SchemaError",
    "StructuredRun",
    "canonical_json",
    "correction_message",
    "extract_json",
    "output_instruction",
    "run_with_output_schema",
    "validate_instance",
    "validate_output_schema",
]
