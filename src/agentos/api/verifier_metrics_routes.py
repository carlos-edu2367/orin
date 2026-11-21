"""HTTP route for verifier pair metrics (`GET /v1/orchestration/verifier/metrics`, WS-A6)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .security import AuthenticatedPrincipal


def _parse_since(raw: str) -> datetime:
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("since must not be empty")
    if cleaned.endswith(("Z", "z")):
        cleaned = f"{cleaned[:-1]}+00:00"
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _resolve_review_store(services: Any) -> Any | None:
    store = getattr(services, "verification_reviews", None)
    if store is not None and not store.__class__.__name__.startswith("_Unavailable"):
        return store

    for attr in ("projects", "conversation_application", "local_workspaces", "context_observations"):
        port = getattr(services, attr, None)
        engine = getattr(port, "_engine", None) if port is not None else None
        if engine is not None:
            from agentos.persistence.postgres.verification_reviews import VerificationReviewStore

            return VerificationReviewStore(engine)

    quota_service = getattr(services, "quota_service", None)
    qstore = getattr(quota_service, "_store", None) if quota_service is not None else None
    engine = getattr(qstore, "_engine", None)
    if engine is not None:
        from agentos.persistence.postgres.verification_reviews import VerificationReviewStore

        return VerificationReviewStore(engine)

    return None


def register_verifier_metrics_routes(
    app: FastAPI,
    services: Any,
    principal_for: Callable[..., AuthenticatedPrincipal],
) -> None:
    """Register `GET /v1/orchestration/verifier/metrics` isolated by principal.user_id."""

    @app.get("/v1/orchestration/verifier/metrics")
    async def get_verifier_metrics(
        request: Request,
        since: str | None = Query(default=None),
        days: int | None = Query(default=None),
    ) -> JSONResponse:
        def _load() -> tuple[int, dict[str, Any]]:
            principal = principal_for(request)
            services.security.check_rate_limit(
                principal,
                action="verifier.metrics.read",
                origin=request.headers.get("origin"),
            )
            services.security.authorize(
                principal,
                action="verifier.metrics.read",
                resource_id=None,
                purpose="verifier.metrics.read",
            )

            since_dt: datetime | None = None
            if since is not None and since.strip():
                try:
                    since_dt = _parse_since(since)
                except ValueError:
                    return (
                        400,
                        {
                            "error": {
                                "category": "VALIDATION",
                                "code": "invalid_since",
                                "message": "query parameter 'since' must be a valid ISO-8601 timestamp",
                                "retryable": False,
                            }
                        },
                    )
            elif days is not None:
                if days <= 0:
                    return (
                        400,
                        {
                            "error": {
                                "category": "VALIDATION",
                                "code": "invalid_days",
                                "message": "query parameter 'days' must be a positive integer",
                                "retryable": False,
                            }
                        },
                    )
                since_dt = datetime.now(UTC) - timedelta(days=days)

            store = _resolve_review_store(services)
            if store is None:
                return 200, {"pairs": [], "since": since_dt.isoformat() if since_dt is not None else None}

            pairs = store.pair_metrics(principal.user_id, since=since_dt)
            return 200, {
                "pairs": pairs,
                "since": since_dt.isoformat() if since_dt is not None else None,
            }

        status_code, payload = await run_in_threadpool(_load)
        return JSONResponse(payload, status_code=status_code)


__all__ = ["register_verifier_metrics_routes"]
