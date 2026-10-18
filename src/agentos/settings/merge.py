"""Deep merge of settings layers."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any


def _identity(item: Any) -> str:
    try:
        return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(item)


def _union(first: list[Any], second: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for item in [*first, *second]:
        key = _identity(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(copy.deepcopy(item))
    return result


def merge_settings(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """``overlay`` has higher precedence. Objects merge, lists union (overlay first), scalars replace."""
    result: dict[str, Any] = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            result[key] = merge_settings(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            result[key] = _union(value, current)
        else:
            result[key] = copy.deepcopy(value)
    return result
