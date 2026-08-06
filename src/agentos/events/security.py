from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType


MAX_PAYLOAD_DEPTH = 4
MAX_PAYLOAD_ITEMS = 32
MAX_PAYLOAD_TEXT = 256
MAX_PAYLOAD_BYTES = 4096

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
    "header",
    "prompt",
    "private",
    "exception",
    "stack_trace",
    "raw_input",
    "raw_output",
    "full_text",
    "proprietary",
)


def _key_is_sensitive(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return normalized in {"token", "credentials", "headers"} or any(
        part in normalized for part in _SENSITIVE_KEY_PARTS
    )


def _freeze(value: object, *, depth: int, count: list[int], field: str) -> object:
    if depth > MAX_PAYLOAD_DEPTH:
        raise ValueError(f"{field} exceeds payload nesting limit")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > MAX_PAYLOAD_TEXT:
            raise ValueError(f"{field} contains text exceeding the payload limit")
        return value
    if isinstance(value, bytes):
        if len(value) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"{field} contains bytes exceeding the payload limit")
        raise ValueError(f"{field} cannot contain raw bytes")
    if isinstance(value, Mapping):
        if len(value) > MAX_PAYLOAD_ITEMS:
            raise ValueError(f"{field} contains too many payload entries")
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{field} keys must be non-blank strings")
            if _key_is_sensitive(key):
                raise ValueError(f"{field} contains a prohibited field")
            count[0] += 1
            if count[0] > MAX_PAYLOAD_ITEMS:
                raise ValueError(f"{field} exceeds the payload item limit")
            frozen[key] = _freeze(item, depth=depth + 1, count=count, field=field)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        if len(value) > MAX_PAYLOAD_ITEMS:
            raise ValueError(f"{field} contains too many payload entries")
        count[0] += len(value)
        if count[0] > MAX_PAYLOAD_ITEMS:
            raise ValueError(f"{field} exceeds the payload item limit")
        return tuple(_freeze(item, depth=depth + 1, count=count, field=field) for item in value)
    raise ValueError(f"{field} contains an unsupported value")


def freeze_payload(payload: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
    frozen = _freeze(payload, depth=0, count=[0], field="payload")
    assert isinstance(frozen, Mapping)
    return frozen


def clearance_allows(clearance: str, classification: str) -> bool:
    order = {"INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3}
    try:
        return order[str(clearance)] >= order[str(classification)]
    except KeyError as exc:
        raise ValueError("unknown data classification") from exc
