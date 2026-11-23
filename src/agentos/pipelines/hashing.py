"""Canonical JSON and the step input hash that lets a resume reuse finished work (WS-C3)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

HASH_DIGEST_BYTES = 32


def _normalize(value: Any, *, paths: bool = False) -> Any:
    """Make a JSON-like value independent of line endings (and of path separators when ``paths``)."""
    if isinstance(value, str):
        text = value.replace("\r\n", "\n").replace("\r", "\n")
        return text.replace("\\", "/") if paths else text
    if isinstance(value, Mapping):
        return {str(key): _normalize(item, paths=paths) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item, paths=paths) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Stable text form: sorted keys, compact separators, unicode kept, ``\\r\\n`` folded to ``\\n``."""
    return json.dumps(_normalize(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def hash_text(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=HASH_DIGEST_BYTES).hexdigest()


def step_input_hash(
    *,
    prompt: str,
    agent: str | None = None,
    agent_digest: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    item: Any = None,
    output_schema: Mapping[str, Any] | None = None,
    isolation: str | None = None,
    budget: Mapping[str, Any] | Sequence[Any] | None = None,
) -> str:
    """Hash of everything that decides what a step run does.

    The step ``description`` and YAML comments are not parameters, so editing them never invalidates a run.
    ``item`` has its path separators folded to ``/`` so a run resumed on another OS keeps its hashes.
    """
    payload = {
        "prompt": _normalize(prompt),
        "agent": agent,
        "agent_digest": agent_digest,
        "provider": provider,
        "model": model,
        "effort": effort,
        "item": _normalize(item, paths=True),
        "output_schema": _normalize(output_schema),
        "isolation": isolation,
        "budget": _normalize(budget),
    }
    return hash_text(canonical_json(payload))


__all__ = ["canonical_json", "hash_text", "step_input_hash"]
