from __future__ import annotations

import json

from agentos.observability import BoundedMetrics, StructuredAuditLogger


def test_structured_log_redacts_sensitive_values_and_keeps_correlation() -> None:
    logger = StructuredAuditLogger()
    line = logger.emit("provider.finished", correlation_id="correlation-1", api_key="do-not-store", prompt="do-not-store", duration_ms=12)

    event = json.loads(line)
    assert event == {"correlation_id": "correlation-1", "duration_ms": 12, "event": "provider.finished"}


def test_metrics_reject_unbounded_or_sensitive_labels() -> None:
    metrics = BoundedMetrics(allowed_labels={"provider": {"openai", "anthropic", "openrouter"}, "outcome": {"success", "failure"}})
    metrics.increment("provider_requests_total", provider="openai", outcome="success")

    assert metrics.snapshot() == {"provider_requests_total|outcome=success|provider=openai": 1}
