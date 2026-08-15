"""Normalize untrusted tool descriptors before they reach the model.

A remote server controls this payload. Anything the runtime cannot bound is
dropped: a missing tool is a visible absence, an unbounded schema is a bug the
provider call pays for.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

from .models import McpToolDescriptor

MAX_TOOLS_PER_SERVER = 128
MAX_DESCRIPTION_CHARS = 1024
MAX_SCHEMA_DEPTH = 8
MAX_SCHEMA_BYTES = 24_000
_TOOL_NAME = re.compile(r"[a-zA-Z0-9_.-]{1,64}")


class UntrustedDescriptorRejected(ValueError):
    """The whole batch is unusable, not just one item."""


def _depth(value: Any, level: int = 0) -> int:
    if level > MAX_SCHEMA_DEPTH:
        return level
    if isinstance(value, Mapping):
        return max((_depth(item, level + 1) for item in value.values()), default=level)
    if isinstance(value, list):
        return max((_depth(item, level + 1) for item in value), default=level)
    return level


def sanitize_tool_descriptors(payload: Iterable[Mapping[str, Any]]) -> tuple[McpToolDescriptor, ...]:
    items = list(payload)
    if len(items) > MAX_TOOLS_PER_SERVER:
        raise UntrustedDescriptorRejected(
            f"the server published {len(items)} tools; the limit is {MAX_TOOLS_PER_SERVER}"
        )
    accepted: list[McpToolDescriptor] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or "")
        if not _TOOL_NAME.fullmatch(name) or name in seen:
            continue
        schema = raw.get("inputSchema") or raw.get("input_schema")
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            continue
        if _depth(schema) > MAX_SCHEMA_DEPTH:
            continue
        try:
            encoded = json.dumps(schema)
        except (TypeError, ValueError):
            continue
        if len(encoded) > MAX_SCHEMA_BYTES:
            continue
        seen.add(name)
        accepted.append(McpToolDescriptor(
            name=name,
            description=str(raw.get("description") or "")[:MAX_DESCRIPTION_CHARS],
            input_schema=json.loads(encoded),
        ))
    return tuple(accepted)


__all__ = [
    "MAX_DESCRIPTION_CHARS", "MAX_SCHEMA_DEPTH", "MAX_TOOLS_PER_SERVER",
    "UntrustedDescriptorRejected", "sanitize_tool_descriptors",
]
