"""Durable SQL store for verification reviews and findings (WS-A6, Spec 5.6)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, asc, insert, select, update
from sqlalchemy.engine import Engine

from agentos.agentic.review_recorder import (
    as_utc,
    compute_pair_metrics,
    normalize_finding_record,
)

from .schema import verification_findings, verification_reviews


def _serialize_finding_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "review_id": str(row["review_id"]),
        "finding_id": str(row["finding_id"]),
        "severity": str(row["severity"]),
        "category": str(row["category"]),
        "has_evidence": bool(row["has_evidence"]),
        "resolution": str(row["resolution"]) if row["resolution"] is not None else None,
        "resolution_reason": str(row["resolution_reason"]) if row["resolution_reason"] is not None else None,
    }


def _serialize_review_row(
    row: Mapping[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_cost = row["cost_usd"]
    return {
        "review_id": str(row["review_id"]),
        "user_id": str(row["user_id"]),
        "conversation_id": str(row["conversation_id"]),
        "turn_id": str(row["turn_id"]),
        "agent_id": str(row["agent_id"] or ""),
        "round": int(row["round"]),
        "trigger_reason": str(row["trigger_reason"]),
        "files_changed": int(row["files_changed"] or 0),
        "lines_changed": int(row["lines_changed"] or 0),
        "author_provider": str(row["author_provider"]),
        "author_model": str(row["author_model"]),
        "author_effort": str(row["author_effort"]) if row["author_effort"] is not None else None,
        "reviewer_provider": str(row["reviewer_provider"]) if row["reviewer_provider"] is not None else None,
        "reviewer_model": str(row["reviewer_model"]) if row["reviewer_model"] is not None else None,
        "reviewer_effort": str(row["reviewer_effort"]) if row["reviewer_effort"] is not None else None,
        "policy": str(row["policy"]),
        "fallback_step": int(row["fallback_step"]),
        "verdict": str(row["verdict"]),
        "input_tokens": int(row["input_tokens"] or 0),
        "output_tokens": int(row["output_tokens"] or 0),
        "cost_usd": float(raw_cost) if raw_cost is not None else None,
        "started_at": as_utc(row["started_at"]),
        "finished_at": as_utc(row["finished_at"]),
        "findings": findings,
    }


class VerificationReviewStore:
    """SQLAlchemy-backed implementation of ``ReviewRecorder`` (A-RF10)."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(UTC))

    def start(
        self,
        *,
        review_id: str | None = None,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        agent_id: str = "",
        round: int = 1,
        trigger_reason: str = "files",
        files_changed: int | None = 0,
        lines_changed: int | None = 0,
        author_provider: str = "",
        author_model: str = "",
        author_effort: str | None = None,
        reviewer_provider: str | None = None,
        reviewer_model: str | None = None,
        reviewer_effort: str | None = None,
        policy: str = "cross_family",
        fallback_step: int = 1,
        started_at: datetime | None = None,
    ) -> str:
        rid = str(review_id).strip()[:64] if review_id and str(review_id).strip() else f"rev-{uuid4().hex[:16]}"
        ts = as_utc(started_at) or as_utc(self._clock()) or datetime.now(UTC)

        values: dict[str, Any] = {
            "review_id": rid,
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
            "turn_id": str(turn_id),
            "agent_id": str(agent_id or ""),
            "round": int(round),
            "trigger_reason": str(trigger_reason)[:32],
            "files_changed": max(0, int(files_changed or 0)),
            "lines_changed": max(0, int(lines_changed or 0)),
            "author_provider": str(author_provider)[:64],
            "author_model": str(author_model)[:128],
            "author_effort": str(author_effort)[:16] if author_effort is not None else None,
            "reviewer_provider": str(reviewer_provider)[:64] if reviewer_provider is not None else None,
            "reviewer_model": str(reviewer_model)[:128] if reviewer_model is not None else None,
            "reviewer_effort": str(reviewer_effort)[:16] if reviewer_effort is not None else None,
            "policy": str(policy)[:16],
            "fallback_step": int(fallback_step),
            "verdict": "pending",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": None,
            "started_at": ts,
            "finished_at": None,
        }
        with self._engine.begin() as conn:
            conn.execute(insert(verification_reviews).values(**values))
        return rid

    start_review = start

    def add_findings(self, review_id: str, findings: Sequence[Any]) -> None:
        rid = str(review_id)
        raw_normalized = [normalize_finding_record(item) for item in findings]
        if not raw_normalized:
            return
        # Deduplicate within the batch keeping last-wins semantics (matching InMemoryReviewRecorder).
        deduped = {rec["finding_id"]: rec for rec in raw_normalized}
        normalized = list(deduped.values())

        t = verification_findings
        with self._engine.begin() as conn:
            existing_ids = {
                str(r[0])
                for r in conn.execute(
                    select(t.c.finding_id).where(t.c.review_id == rid)
                ).all()
            }
            to_insert: list[dict[str, Any]] = []
            for rec in normalized:
                fid = rec["finding_id"]
                if fid in existing_ids:
                    conn.execute(
                        update(t)
                        .where(and_(t.c.review_id == rid, t.c.finding_id == fid))
                        .values(
                            severity=rec["severity"],
                            category=rec["category"],
                            has_evidence=rec["has_evidence"],
                            resolution=rec["resolution"],
                            resolution_reason=rec["resolution_reason"],
                        )
                    )
                else:
                    existing_ids.add(fid)
                    to_insert.append({"review_id": rid, **rec})
            if to_insert:
                conn.execute(insert(t), to_insert)

    def set_resolution(
        self,
        review_id: str,
        finding_id: str,
        resolution: str,
        reason: str | None = None,
    ) -> None:
        rid = str(review_id)
        fid = str(finding_id).strip()[:64]
        res = str(resolution).strip().lower()[:16]
        reason_val = str(reason)[:1000] if reason is not None else None

        t = verification_findings
        with self._engine.begin() as conn:
            conn.execute(
                update(t)
                .where(and_(t.c.review_id == rid, t.c.finding_id == fid))
                .values(resolution=res, resolution_reason=reason_val)
            )

    def finish(
        self,
        review_id: str,
        *,
        verdict: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | Decimal | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        rid = str(review_id)
        ts = as_utc(finished_at) or as_utc(self._clock()) or datetime.now(UTC)
        # Never invent 0.0 when cost_usd is unavailable (subscription plans).
        cost_val = Decimal(str(cost_usd)) if cost_usd is not None else None

        t = verification_reviews
        with self._engine.begin() as conn:
            conn.execute(
                update(t)
                .where(t.c.review_id == rid)
                .values(
                    verdict=str(verdict).strip().lower()[:16],
                    input_tokens=max(0, int(input_tokens or 0)),
                    output_tokens=max(0, int(output_tokens or 0)),
                    cost_usd=cost_val,
                    finished_at=ts,
                )
            )

    finish_review = finish

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        rid = str(review_id)
        t_rev = verification_reviews
        t_find = verification_findings
        with self._engine.connect() as conn:
            rev_row = conn.execute(
                select(t_rev).where(t_rev.c.review_id == rid)
            ).mappings().first()
            if rev_row is None:
                return None
            finding_rows = conn.execute(
                select(t_find)
                .where(t_find.c.review_id == rid)
                .order_by(asc(t_find.c.finding_id))
            ).mappings().all()
        return _serialize_review_row(rev_row, [_serialize_finding_row(r) for r in finding_rows])

    def list_reviews(
        self,
        user_id: str,
        conversation_id: str | None = None,
        turn_id: str | None = None,
    ) -> list[dict[str, Any]]:
        t_rev = verification_reviews
        t_find = verification_findings
        conditions = [t_rev.c.user_id == str(user_id)]
        if conversation_id is not None:
            conditions.append(t_rev.c.conversation_id == str(conversation_id))
        if turn_id is not None:
            conditions.append(t_rev.c.turn_id == str(turn_id))

        with self._engine.connect() as conn:
            rev_rows = conn.execute(
                select(t_rev)
                .where(and_(*conditions))
                .order_by(asc(t_rev.c.started_at), asc(t_rev.c.review_id))
            ).mappings().all()
            if not rev_rows:
                return []
            review_ids = [str(r["review_id"]) for r in rev_rows]
            finding_rows = conn.execute(
                select(t_find)
                .where(t_find.c.review_id.in_(review_ids))
                .order_by(asc(t_find.c.review_id), asc(t_find.c.finding_id))
            ).mappings().all()

        findings_by_review: dict[str, list[dict[str, Any]]] = {}
        for frow in finding_rows:
            findings_by_review.setdefault(str(frow["review_id"]), []).append(_serialize_finding_row(frow))

        return [
            _serialize_review_row(r, findings_by_review.get(str(r["review_id"]), []))
            for r in rev_rows
        ]

    def pair_metrics(
        self,
        user_id: str,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        t_rev = verification_reviews
        t_find = verification_findings
        since_utc = as_utc(since)

        conditions = [
            t_rev.c.user_id == str(user_id),
            t_rev.c.verdict != "skipped",
            t_rev.c.reviewer_provider.is_not(None),
            t_rev.c.reviewer_model.is_not(None),
        ]
        if since_utc is not None:
            conditions.append(t_rev.c.started_at >= since_utc)

        with self._engine.connect() as conn:
            rev_rows = conn.execute(
                select(t_rev).where(and_(*conditions))
            ).mappings().all()

            filtered_reviews: list[Mapping[str, Any]] = []
            for row in rev_rows:
                if since_utc is not None:
                    started = as_utc(row["started_at"])
                    if started is None or started < since_utc:
                        continue
                filtered_reviews.append(row)

            if not filtered_reviews:
                return []

            review_ids = [str(r["review_id"]) for r in filtered_reviews]
            finding_rows = conn.execute(
                select(t_find).where(t_find.c.review_id.in_(review_ids))
            ).mappings().all()

        findings_by_review: dict[str, list[Mapping[str, Any]]] = {}
        for frow in finding_rows:
            findings_by_review.setdefault(str(frow["review_id"]), []).append(frow)

        return compute_pair_metrics(filtered_reviews, findings_by_review)


__all__ = ["VerificationReviewStore"]
