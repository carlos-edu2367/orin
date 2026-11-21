"""Tests for GET /v1/orchestration/verifier/metrics (WS-A6)."""

from __future__ import annotations

import asyncio
import gc
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine

from agentos.agentic.review_recorder import InMemoryReviewRecorder
from agentos.agentic.verification_findings import Evidence, Finding
from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.verification_reviews import VerificationReviewStore


def _make_app(verification_reviews=None):
    security = InMemorySecurityService()
    security.add_pat("pat-u1", AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})))
    security.add_pat("pat-u2", AuthenticatedPrincipal("user-2", "cred-2", frozenset({"api"})))
    return create_app(
        ApiServices(
            security=security,
            verification_reviews=verification_reviews,
        )
    )


def _proven(fid: str) -> Finding:
    return Finding(
        id=fid,
        severity="major",
        original_severity="major",
        category="bug",
        claim="bug found",
        evidence=Evidence(kind="code_ref", file="a.py", line=5, reason="bad"),
        has_evidence=True,
    )


def test_verifier_metrics_route_isolates_user_id_and_supports_since_and_days(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'verifier_metrics.db').as_posix()}")
    upgrade(engine, "head")
    store = VerificationReviewStore(engine)

    now = datetime.now(UTC)
    old_ts = now - timedelta(days=45)
    recent_ts = now - timedelta(days=2)

    # user-1 old review
    r1 = store.start(
        user_id="user-1",
        conversation_id="c1",
        turn_id="t1",
        lines_changed=100,
        author_provider="anthropic-cli",
        author_model="claude-opus-5-5",
        reviewer_provider="openai-codex",
        reviewer_model="gpt-6-sol",
        started_at=old_ts,
    )
    store.add_findings(r1, [_proven("F1")])
    store.set_resolution(r1, "F1", "rejected", reason="fp")
    store.finish(r1, verdict="fail", input_tokens=500, output_tokens=100, cost_usd=Decimal("0.05"))

    # user-1 recent review
    r2 = store.start(
        user_id="user-1",
        conversation_id="c1",
        turn_id="t2",
        lines_changed=100,
        author_provider="anthropic-cli",
        author_model="claude-opus-5-5",
        reviewer_provider="openai-codex",
        reviewer_model="gpt-6-sol",
        started_at=recent_ts,
    )
    store.add_findings(r2, [_proven("F1")])
    store.set_resolution(r2, "F1", "confirmed")
    store.finish(r2, verdict="fail", input_tokens=700, output_tokens=300, cost_usd=None)

    # user-2 review (must never appear in user-1's metrics)
    r3 = store.start(
        user_id="user-2",
        conversation_id="c2",
        turn_id="t1",
        lines_changed=200,
        author_provider="openai-codex",
        author_model="gpt-6-sol",
        reviewer_provider="anthropic-cli",
        reviewer_model="claude-opus-5-5",
        started_at=recent_ts,
    )
    store.finish(r3, verdict="pass", input_tokens=1000, output_tokens=200)

    app = _make_app(verification_reviews=store)
    client = TestClient(app)

    # Unauthenticated -> 401
    unauth = client.get("/v1/orchestration/verifier/metrics")
    assert unauth.status_code == 401

    # user-1 all-time
    res1 = client.get("/v1/orchestration/verifier/metrics", headers={"Authorization": "Bearer pat-u1"})
    assert res1.status_code == 200
    body1 = res1.json()
    assert len(body1["pairs"]) == 1
    pair1 = body1["pairs"][0]
    assert pair1["author_model"] == "claude-opus-5-5"
    assert pair1["reviewer_model"] == "gpt-6-sol"
    assert pair1["reviews_count"] == 2
    assert pair1["confirmed_rate"] == 0.5
    assert pair1["rejected_rate"] == 0.5
    assert pair1["avg_cost_usd"] == 0.05

    # user-1 filtered by days=7 -> only r2
    res1_days = client.get(
        "/v1/orchestration/verifier/metrics?days=7",
        headers={"Authorization": "Bearer pat-u1"},
    )
    assert res1_days.status_code == 200
    body1_days = res1_days.json()
    assert len(body1_days["pairs"]) == 1
    assert body1_days["pairs"][0]["reviews_count"] == 1
    assert body1_days["pairs"][0]["confirmed_rate"] == 1.0
    assert body1_days["pairs"][0]["avg_cost_usd"] is None
    assert body1_days["since"] is not None

    # user-1 filtered by explicit ISO since
    since_iso = (now - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    res1_since = client.get(
        f"/v1/orchestration/verifier/metrics?since={since_iso}",
        headers={"Authorization": "Bearer pat-u1"},
    )
    assert res1_since.status_code == 200
    assert res1_since.json()["pairs"][0]["reviews_count"] == 1

    # Invalid since -> 400
    res_bad = client.get(
        "/v1/orchestration/verifier/metrics?since=not-a-date",
        headers={"Authorization": "Bearer pat-u1"},
    )
    assert res_bad.status_code == 400

    # user-2 sees only their own pair
    res2 = client.get("/v1/orchestration/verifier/metrics", headers={"Authorization": "Bearer pat-u2"})
    assert res2.status_code == 200
    body2 = res2.json()
    assert len(body2["pairs"]) == 1
    assert body2["pairs"][0]["author_model"] == "gpt-6-sol"
    assert body2["pairs"][0]["reviewer_model"] == "claude-opus-5-5"
    assert body2["pairs"][0]["reviews_count"] == 1


def test_verifier_metrics_route_empty_when_no_store_configured() -> None:
    app = _make_app(verification_reviews=None)
    client = TestClient(app)
    res = client.get("/v1/orchestration/verifier/metrics", headers={"Authorization": "Bearer pat-u1"})
    assert res.status_code == 200
    assert res.json() == {"pairs": [], "since": None}


def test_verifier_metrics_route_does_not_block_the_event_loop() -> None:
    slow_seconds = 0.8

    class SlowReviewStore(InMemoryReviewRecorder):
        def pair_metrics(self, user_id: str, since: datetime | None = None):
            time.sleep(slow_seconds)
            return super().pair_metrics(user_id, since=since)

    app = _make_app(verification_reviews=SlowReviewStore())

    async def _run() -> float:
        transport = ASGITransport(app=app)
        gc.collect()
        gc_was_enabled = gc.isenabled()
        gc.disable()
        try:
            started = time.monotonic()
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                slow = asyncio.ensure_future(
                    client.get(
                        "/v1/orchestration/verifier/metrics",
                        headers={"Authorization": "Bearer pat-u1"},
                    )
                )
                await asyncio.sleep(slow_seconds / 4)
                fast_resp = await client.get(
                    "/v1/quota",
                    headers={"Authorization": "Bearer pat-u1"},
                )
                assert fast_resp.status_code == 200
                fast_elapsed = time.monotonic() - started
                slow_resp = await slow
                assert slow_resp.status_code == 200
                return fast_elapsed
        finally:
            if gc_was_enabled:
                gc.enable()

    fast_completed_at = asyncio.run(_run())
    assert fast_completed_at < slow_seconds / 2, (
        "a concurrent GET should not wait for a slow verifier metrics query — the event loop is blocked"
    )
