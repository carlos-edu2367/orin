from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from hashlib import sha256
import json

from agentos.events import DataClassification


class MultiAgentError(ValueError):
    pass


class MultiAgentAccessDenied(PermissionError, MultiAgentError):
    pass


class MultiAgentIdempotencyConflict(MultiAgentError):
    pass


class MultiAgentValidationError(MultiAgentError):
    pass


def require_same_scope(*, expected_user_id: str, expected_workspace_id: str | None, actual_user_id: str, actual_workspace_id: str | None) -> None:
    if expected_user_id != actual_user_id or expected_workspace_id != actual_workspace_id:
        raise MultiAgentAccessDenied("ownership scope rejected")


def classification_allows(ceiling: DataClassification, value: DataClassification) -> bool:
    order = {
        DataClassification.INTERNAL: 0,
        DataClassification.CONFIDENTIAL: 1,
        DataClassification.RESTRICTED: 2,
    }
    return order[DataClassification(ceiling)] >= order[DataClassification(value)]


def _plain(value: object, depth: int = 0) -> object:
    if depth > 5:
        raise MultiAgentValidationError("fingerprint payload exceeds depth")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _plain(item, depth + 1) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _plain(item, depth + 1) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        if len(value) > 64:
            raise MultiAgentValidationError("fingerprint collection exceeds bound")
        return [_plain(item, depth + 1) for item in value]
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def fingerprint(value: object) -> str:
    encoded = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


def sanitize_error(_: object) -> str:
    return "multi-agent operation rejected"


__all__ = [
    "MultiAgentAccessDenied", "MultiAgentError", "MultiAgentIdempotencyConflict",
    "MultiAgentValidationError", "classification_allows", "fingerprint",
    "require_same_scope", "sanitize_error",
]
