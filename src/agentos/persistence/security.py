from __future__ import annotations

from agentos.events.models import DataClassification
from agentos.events.security import clearance_allows


def classification_allows(
    ceiling: DataClassification, classification: DataClassification
) -> bool:
    return clearance_allows(ceiling.value, classification.value)


def scope_matches(record_context, operation_context) -> bool:
    return record_context.scope_key() == operation_context.scope_key()


__all__ = ["classification_allows", "scope_matches"]
