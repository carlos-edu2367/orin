from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from agentos.events.models import DataClassification
from agentos.events.security import clearance_allows


_MAX_SNAPSHOT_DEPTH = 6
_MAX_SNAPSHOT_ITEMS = 128
_MAX_SNAPSHOT_TEXT = 4096
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "credential",
    "cookie",
    "authorization",
    "prompt",
    "private_key",
    "stack_trace",
    "raw_input",
    "raw_output",
    "proprietary",
)


def freeze_persistence_payload(payload: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("persistence data must be a mapping")
    count = [0]

    def freeze(value: object, depth: int) -> object:
        if depth > _MAX_SNAPSHOT_DEPTH:
            raise ValueError("persistence data exceeds nesting limit")
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) > _MAX_SNAPSHOT_TEXT:
                raise ValueError("persistence data text exceeds limit")
            return value
        if isinstance(value, Mapping):
            if len(value) > _MAX_SNAPSHOT_ITEMS:
                raise ValueError("persistence data contains too many entries")
            frozen: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str) or not key.strip():
                    raise ValueError("persistence data keys must be non-blank strings")
                normalized = key.lower().replace("-", "_")
                if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                    raise ValueError("persistence data contains protected data")
                count[0] += 1
                if count[0] > _MAX_SNAPSHOT_ITEMS:
                    raise ValueError("persistence data exceeds item limit")
                frozen[key] = freeze(item, depth + 1)
            return MappingProxyType(frozen)
        if isinstance(value, (tuple, list)):
            count[0] += len(value)
            if count[0] > _MAX_SNAPSHOT_ITEMS:
                raise ValueError("persistence data exceeds item limit")
            return tuple(freeze(item, depth + 1) for item in value)
        raise ValueError("persistence data contains unsupported data")

    result = freeze(payload, 0)
    assert isinstance(result, Mapping)
    return result


def classification_allows(
    ceiling: DataClassification, classification: DataClassification
) -> bool:
    return clearance_allows(ceiling.value, classification.value)


def scope_matches(record_context, operation_context) -> bool:
    return record_context.scope_key() == operation_context.scope_key()


__all__ = ["classification_allows", "freeze_persistence_payload", "scope_matches"]
