"""Tests for ReviewRecorder, InMemoryReviewRecorder, and VerificationReviewStore (WS-A6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select

from agentos.agentic.review_recorder import InMemoryReviewRecorder, ReviewRecorder
from agentos.agentic.verification_findings import Evidence, Finding
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.schema import verification_findings, verification_reviews
from agentos.persistence.postgres.verification_reviews import VerificationReviewStore


def _make_engine():
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine, "head")
    return engine


def _proven_finding(fid: str, *, severity: str = "blocker", category: str = "bug") -> Finding:
    return Finding(
        id=fid,
        severity=severity,
        original_severity=severity,
        category=category,
        claim=f"Finding {fid} claim",
        evidence=Evidence(kind="code_ref", file="src/app.py", line=10, reason="broken branch"),
        has_evidence=True,
    )


def _unproven_finding(fid: str) -> Finding:
    return Finding(
        id=fid,
        severity="unproven",
        original_severity="major",
        category="consistency",
        claim=f"Unproven {fid}",
        evidence=None,
        has_evidence=False,
    )


def test_review_recorder_protocol_and_no_sqlalchemy_in_agentic_module() -> None:
    import inspect

    import agentos.agentic.review_recorder as mod

    source = inspect.getsource(mod).lower()
    assert "sqlalchemy" not in source
    assert "alembic" not in source

    mem = InMemoryReviewRecorder()
    assert isinstance(mem, ReviewRecorder)

    store = VerificationReviewStore(_make_engine())
    assert isinstance(store, ReviewRecorder)


def test_criterion_6_review_rows_findings_resolutions_skipped_and_null_cost() -> None:
    engine = _make_engine()
    t0 = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    store = VerificationReviewStore(engine, clock=lambda: t0)

    review_id = store.start(
        user_id="user-1",
        conversation_id="conv-1",
        turn_id="turn-1",
        agent_id="agent-main",
        round=1,
        trigger_reason="files",
        files_changed=4,
        lines_changed=120,
        author_provider="anthropic-cli",
        author_model="claude-opus-5-5",
        author_effort="medium",
        reviewer_provider="openai-codex",
        reviewer_model="gpt-6-sol",
        reviewer_effort="high",
        policy="cross_family",
        fallback_step=1,
        started_at=t0,
    )

    store.add_findings(
        review_id,
        [
            _proven_finding("F1", severity="blocker", category="bug"),
            _unproven_finding("F2"),
            {
                "finding_id": "F3",
                "severity": "major",
                "category": "security",
                "has_evidence": True,
            },
        ],
    )
    store.set_resolution(review_id, "F1", "fixed", reason="Added null check and regression test.")
    store.set_resolution(review_id, "F3", "rejected", reason="False positive: guarded upstream.")
    store.finish(
        review_id,
        verdict="fail",
        input_tokens=1500,
        output_tokens=400,
        cost_usd=None,
        finished_at=t0 + timedelta(seconds=12),
    )

    # Skipped review (fallback step 4) has reviewer_model=None and verdict="skipped"
    skipped_id = store.start(
        user_id="user-1",
        conversation_id="conv-1",
        turn_id="turn-2",
        agent_id="agent-main",
        round=1,
        trigger_reason="sensitive_path",
        files_changed=1,
        lines_changed=10,
        author_provider="anthropic-cli",
        author_model="claude-opus-5-5",
        author_effort="high",
        reviewer_provider=None,
        reviewer_model=None,
        reviewer_effort=None,
        policy="cross_family",
        fallback_step=4,
        started_at=t0 + timedelta(minutes=1),
    )
    store.finish(skipped_id, verdict="skipped", finished_at=t0 + timedelta(minutes=1, seconds=1))

    with engine.connect() as conn:
        review_rows = conn.execute(select(verification_reviews)).mappings().all()
        finding_rows = conn.execute(select(verification_findings)).mappings().all()

    assert len(review_rows) == 2
    by_rev = {r["review_id"]: r for r in review_rows}

    main_rev = by_rev[review_id]
    assert main_rev["user_id"] == "user-1"
    assert main_rev["verdict"] == "fail"
    assert main_rev["input_tokens"] == 1500
    assert main_rev["output_tokens"] == 400
    assert main_rev["cost_usd"] is None
    assert main_rev["finished_at"] is not None

    skip_rev = by_rev[skipped_id]
    assert skip_rev["verdict"] == "skipped"
    assert skip_rev["fallback_step"] == 4
    assert skip_rev["reviewer_provider"] is None
    assert skip_rev["reviewer_model"] is None
    assert skip_rev["cost_usd"] is None

    assert len(finding_rows) == 3
    by_fid = {r["finding_id"]: r for r in finding_rows}
    assert by_fid["F1"]["severity"] == "blocker"
    assert by_fid["F1"]["has_evidence"] is True
    assert by_fid["F1"]["resolution"] == "fixed"
    assert by_fid["F1"]["resolution_reason"] == "Added null check and regression test."

    assert by_fid["F2"]["severity"] == "unproven"
    assert by_fid["F2"]["has_evidence"] is False
    assert by_fid["F2"]["resolution"] is None

    assert by_fid["F3"]["resolution"] == "rejected"
    assert by_fid["F3"]["resolution_reason"] == "False positive: guarded upstream."

    listed = store.list_reviews("user-1", conversation_id="conv-1")
    assert len(listed) == 2
    by_listed = {item["review_id"]: item for item in listed}
    assert len(by_listed[review_id]["findings"]) == 3
    assert len(by_listed[skipped_id]["findings"]) == 0


@pytest.mark.parametrize("backend", ["sql", "memory"])
def test_pair_metrics_exact_numbers_two_pairs_and_since_filter(backend: str) -> None:
    if backend == "sql":
        recorder: ReviewRecorder = VerificationReviewStore(_make_engine())
    else:
        recorder = InMemoryReviewRecorder()

    t_old = datetime(2026, 11, 1, 10, 0, 0, tzinfo=UTC)
    t_recent_1 = datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)
    t_recent_2 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=UTC)

    # Pair A (Claude -> Codex): 2 reviews (1 old, 1 recent)
    # Review A1 (old): 100 lines, 1000+500 tokens, cost=0.10, verdict=fail
    #   findings: F1 proven confirmed, F2 proven rejected, F3 unproven open
    r_a1 = recorder.start(
        user_id="user-1",
        conversation_id="c1",
        turn_id="t1",
        round=1,
        trigger_reason="files",
        files_changed=3,
        lines_changed=100,
        author_provider="anthropic-cli",
        author_model="claude-opus-5-5",
        reviewer_provider="openai-codex",
        reviewer_model="gpt-6-sol",
        policy="cross_family",
        fallback_step=1,
        started_at=t_old,
    )
    recorder.add_findings(
        r_a1,
        [
            _proven_finding("F1", severity="blocker"),
            _proven_finding("F2", severity="major"),
            _unproven_finding("F3"),
        ],
    )
    recorder.set_resolution(r_a1, "F1", "confirmed", reason="Confirmed bug")
    recorder.set_resolution(r_a1, "F2", "rejected", reason="Not an issue")
    recorder.finish(
        r_a1,
        verdict="fail",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=Decimal("0.100000"),
        finished_at=t_old + timedelta(seconds=20),
    )

    # Review A2 (recent): 300 lines, 2000+500 tokens, cost=None (subscription), verdict=pass
    #   findings: F1 proven fixed
    r_a2 = recorder.start(
        user_id="user-1",
        conversation_id="c1",
        turn_id="t2",
        round=2,
        trigger_reason="lines",
        files_changed=5,
        lines_changed=300,
        author_provider="anthropic-cli",
        author_model="claude-opus-5-5",
        reviewer_provider="openai-codex",
        reviewer_model="gpt-6-sol",
        policy="cross_family",
        fallback_step=1,
        started_at=t_recent_1,
    )
    recorder.add_findings(r_a2, [_proven_finding("F1", severity="major")])
    recorder.set_resolution(r_a2, "F1", "fixed", reason="Fixed in round 2")
    recorder.finish(
        r_a2,
        verdict="pass",
        input_tokens=2000,
        output_tokens=500,
        cost_usd=None,
        finished_at=t_recent_1 + timedelta(seconds=15),
    )

    # Pair B (Codex -> Antigravity): 1 recent review, 200 lines, 800+200 tokens, cost=None, verdict=inconclusive
    #   findings: 2 proven (1 confirmed, 1 rejected) -> confirmed_rate=0.5, rejected_rate=0.5
    r_b1 = recorder.start(
        user_id="user-1",
        conversation_id="c2",
        turn_id="t1",
        round=1,
        trigger_reason="pipeline",
        files_changed=2,
        lines_changed=200,
        author_provider="openai-codex",
        author_model="gpt-6-sol",
        reviewer_provider="google-antigravity",
        reviewer_model="gemini-4-neon-high",
        policy="cross_family",
        fallback_step=1,
        started_at=t_recent_2,
    )
    recorder.add_findings(
        r_b1,
        [
            _proven_finding("F1", severity="major"),
            _proven_finding("F2", severity="minor"),
        ],
    )
    recorder.set_resolution(r_b1, "F1", "confirmed")
    recorder.set_resolution(r_b1, "F2", "rejected", reason="Intentional")
    recorder.finish(
        r_b1,
        verdict="inconclusive",
        input_tokens=800,
        output_tokens=200,
        cost_usd=None,
        finished_at=t_recent_2 + timedelta(seconds=10),
    )

    # Skipped review for user-1 (must not create a bogus None reviewer pair)
    r_skip = recorder.start(
        user_id="user-1",
        conversation_id="c2",
        turn_id="t2",
        round=1,
        trigger_reason="files",
        files_changed=3,
        lines_changed=50,
        author_provider="anthropic-cli",
        author_model="claude-opus-5-5",
        reviewer_provider=None,
        reviewer_model=None,
        policy="cross_family",
        fallback_step=4,
        started_at=t_recent_2,
    )
    recorder.finish(r_skip, verdict="skipped")

    # Another user's review (must be completely isolated from user-1)
    r_other = recorder.start(
        user_id="user-2",
        conversation_id="c9",
        turn_id="t9",
        round=1,
        trigger_reason="files",
        files_changed=10,
        lines_changed=500,
        author_provider="anthropic-cli",
        author_model="claude-opus-5-5",
        reviewer_provider="openai-codex",
        reviewer_model="gpt-6-sol",
        policy="cross_family",
        fallback_step=1,
        started_at=t_recent_2,
    )
    recorder.finish(r_other, verdict="pass", input_tokens=9999, output_tokens=1, cost_usd=9.99)

    # All-time metrics for user-1
    metrics = recorder.pair_metrics("user-1")
    assert len(metrics) == 2

    by_pair = {
        (m["author_provider"], m["author_model"], m["reviewer_provider"], m["reviewer_model"]): m
        for m in metrics
    }

    pair_a = by_pair[("anthropic-cli", "claude-opus-5-5", "openai-codex", "gpt-6-sol")]
    assert pair_a["reviews_count"] == 2
    assert pair_a["pass_count"] == 1
    assert pair_a["fail_count"] == 1
    assert pair_a["inconclusive_count"] == 0
    assert pair_a["total_findings"] == 4
    assert pair_a["proven_findings"] == 3
    assert pair_a["unproven_findings"] == 1
    assert pair_a["confirmed_count"] == 1
    assert pair_a["fixed_count"] == 1
    assert pair_a["rejected_count"] == 1
    assert pair_a["open_count"] == 1
    # 2 accepted (1 confirmed + 1 fixed) out of 3 resolved -> 2/3
    assert pair_a["confirmed_rate"] == pytest.approx(2 / 3, rel=1e-5)
    assert pair_a["accepted_rate"] == pytest.approx(2 / 3, rel=1e-5)
    assert pair_a["rejected_rate"] == pytest.approx(1 / 3, rel=1e-5)
    # 4 total findings across 400 lines -> 1.0 per 100 lines
    assert pair_a["findings_per_100_lines"] == pytest.approx(1.0)
    # (1500 + 2500) = 4000 total, avg = 2000.0
    assert pair_a["total_tokens"] == 4000
    assert pair_a["avg_tokens"] == pytest.approx(2000.0)
    # Only 1 of 2 reviews reported cost (0.10); avg over reported = 0.10, total = 0.10
    assert pair_a["total_cost_usd"] == pytest.approx(0.10)
    assert pair_a["avg_cost_usd"] == pytest.approx(0.10)

    pair_b = by_pair[("openai-codex", "gpt-6-sol", "google-antigravity", "gemini-4-neon-high")]
    assert pair_b["reviews_count"] == 1
    assert pair_b["pass_count"] == 0
    assert pair_b["fail_count"] == 0
    assert pair_b["inconclusive_count"] == 1
    assert pair_b["total_findings"] == 2
    assert pair_b["proven_findings"] == 2
    assert pair_b["unproven_findings"] == 0
    assert pair_b["confirmed_count"] == 1
    assert pair_b["fixed_count"] == 0
    assert pair_b["rejected_count"] == 1
    assert pair_b["open_count"] == 0
    assert pair_b["confirmed_rate"] == pytest.approx(0.5)
    assert pair_b["rejected_rate"] == pytest.approx(0.5)
    assert pair_b["findings_per_100_lines"] == pytest.approx(1.0)
    assert pair_b["total_tokens"] == 1000
    assert pair_b["avg_tokens"] == pytest.approx(1000.0)
    # No review reported cost_usd -> must remain None, never 0.0
    assert pair_b["total_cost_usd"] is None
    assert pair_b["avg_cost_usd"] is None

    # Filter with since: excludes A1 (t_old), keeps A2 and B1
    recent_metrics = recorder.pair_metrics("user-1", since=datetime(2026, 11, 10, 0, 0, 0, tzinfo=UTC))
    by_pair_recent = {
        (m["author_provider"], m["author_model"], m["reviewer_provider"], m["reviewer_model"]): m
        for m in recent_metrics
    }
    assert set(by_pair_recent.keys()) == set(by_pair.keys())
    pair_a_recent = by_pair_recent[("anthropic-cli", "claude-opus-5-5", "openai-codex", "gpt-6-sol")]
    assert pair_a_recent["reviews_count"] == 1
    assert pair_a_recent["pass_count"] == 1
    assert pair_a_recent["fail_count"] == 0
    assert pair_a_recent["total_findings"] == 1
    assert pair_a_recent["fixed_count"] == 1
    assert pair_a_recent["confirmed_rate"] == pytest.approx(1.0)
    assert pair_a_recent["rejected_rate"] == pytest.approx(0.0)
    assert pair_a_recent["total_tokens"] == 2500
    assert pair_a_recent["avg_cost_usd"] is None
    assert pair_a_recent["total_cost_usd"] is None


def test_aliases_start_review_and_finish_review_work() -> None:
    store = VerificationReviewStore(_make_engine())
    rid = store.start_review(
        user_id="u1",
        conversation_id="c1",
        turn_id="t1",
        author_provider="anthropic-cli",
        author_model="claude-opus-5-5",
        reviewer_provider="openai-codex",
        reviewer_model="gpt-6-sol",
    )
    store.finish_review(rid, verdict="pass", input_tokens=100, output_tokens=50)
    metrics = store.pair_metrics("u1")
    assert len(metrics) == 1
    assert metrics[0]["total_tokens"] == 150
