"""Canonical JSON and step input hash (WS-C3)."""

from __future__ import annotations

from agentos.pipelines.hashing import canonical_json, step_input_hash

BASE = dict(
    prompt="Implemente a tarefa\nem duas etapas",
    agent="builder",
    agent_digest="abc123",
    provider="anthropic-cli",
    model="claude-sonnet-5-5",
    effort="medium",
    item={"file": "src/app.py", "n": 1},
    output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    isolation="worktree",
    budget={"max_tokens": 1000, "max_seconds": 60},
)


def _hash(**overrides: object) -> str:
    return step_input_hash(**{**BASE, **overrides})


def test_canonical_json_is_compact_sorted_and_keeps_unicode() -> None:
    assert canonical_json({"b": 1, "a": "ação"}) == '{"a":"ação","b":1}'


def test_canonical_json_normalizes_line_endings_in_strings() -> None:
    assert canonical_json({"t": "a\r\nb\rc"}) == canonical_json({"t": "a\nb\nc"})


def test_hash_is_64_hex_chars() -> None:
    value = _hash()
    assert len(value) == 64
    int(value, 16)


def test_hash_ignores_key_order_and_crlf() -> None:
    reordered = {"max_seconds": 60, "max_tokens": 1000}
    assert _hash(budget=reordered) == _hash()
    assert _hash(prompt=BASE["prompt"].replace("\n", "\r\n")) == _hash()
    assert _hash(item={"n": 1, "file": "src/app.py"}) == _hash()


def test_hash_changes_with_every_input_that_matters() -> None:
    reference = _hash()
    variants = {
        "prompt": "outro prompt",
        "agent": "reviewer",
        "agent_digest": "zzz",
        "provider": "openai-codex",
        "model": "gpt-6-luna",
        "effort": "high",
        "item": {"file": "src/other.py", "n": 1},
        "output_schema": {"type": "object"},
        "isolation": "none",
        "budget": {"max_tokens": 2000},
    }
    seen = {reference}
    for name, value in variants.items():
        changed = _hash(**{name: value})
        assert changed != reference, name
        seen.add(changed)
    assert len(seen) == len(variants) + 1


def test_hash_ignores_what_is_not_an_input() -> None:
    # `description` and comments are not parameters of the hash at all.
    import inspect

    parameters = set(inspect.signature(step_input_hash).parameters)
    assert "description" not in parameters
    assert "comment" not in parameters


def test_item_paths_with_either_separator_hash_the_same() -> None:
    assert _hash(item="src\\pkg\\mod.py") == _hash(item="src/pkg/mod.py")
    assert _hash(item={"path": "a\\b"}) == _hash(item={"path": "a/b"})


def test_optional_fields_default_to_none() -> None:
    assert step_input_hash(prompt="x") == step_input_hash(prompt="x", agent=None, item=None)
