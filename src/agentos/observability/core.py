from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import re
from typing import Iterable, Mapping


_SENSITIVE_FIELD = re.compile(r"(?:api[_-]?key|secret|token|password|cookie|prompt|response|dsn|sql|path|url)", re.IGNORECASE)


class StructuredAuditLogger:
    """Creates JSON records containing only allowlisted, non-content fields."""

    def emit(self, event: str, **fields: object) -> str:
        if not event or len(event) > 120:
            raise ValueError("event must be a bounded identifier")
        record: dict[str, object] = {"event": event}
        for name, value in fields.items():
            if _SENSITIVE_FIELD.search(name) or value is None:
                continue
            if isinstance(value, (str, int, float, bool)) and len(str(value)) <= 128:
                record[name] = value
        return json.dumps(record, sort_keys=True, separators=(",", ":"))


@dataclass(slots=True)
class BoundedMetrics:
    """In-process metric adapter that refuses sensitive and unbounded labels."""

    allowed_labels: Mapping[str, set[str]] = field(default_factory=dict)
    _counts: Counter[str] = field(default_factory=Counter, init=False, repr=False)

    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        if not name or value < 0:
            raise ValueError("metric name and value are invalid")
        normalized: list[tuple[str, str]] = []
        for label, label_value in labels.items():
            if _SENSITIVE_FIELD.search(label) or label not in self.allowed_labels or label_value not in self.allowed_labels[label]:
                raise ValueError("metric label is not bounded")
            normalized.append((label, label_value))
        suffix = "|".join(f"{key}={item}" for key, item in sorted(normalized))
        self._counts[f"{name}|{suffix}" if suffix else name] += value

    def snapshot(self) -> dict[str, int]:
        return dict(self._counts)


class ExecutionReconstructor:
    """Produces an explicitly sourced, content-free execution audit timeline."""

    def reconstruct(self, events: Iterable[Mapping[str, object]], *, execution_id: str) -> tuple[dict[str, object], ...]:
        timeline: list[dict[str, object]] = []
        for event in events:
            if str(event.get("execution_id", "")) != execution_id:
                continue
            timeline.append({
                "event_id": event.get("event_id"),
                "event_type": event.get("event_type"),
                "occurred_at": event.get("occurred_at"),
                "source": "durable_event",
            })
        return tuple(timeline)


__all__ = ["BoundedMetrics", "ExecutionReconstructor", "StructuredAuditLogger"]
