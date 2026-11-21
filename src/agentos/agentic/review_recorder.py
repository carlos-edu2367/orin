"""Protocol and in-memory implementation for recording verification reviews (WS-A6, Spec 5.6).

This module belongs to the domain layer and intentionally has zero database-driver
or migration-framework dependencies.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4


def as_utc(dt: datetime | None) -> datetime | None:
    """Normalize a datetime to timezone-aware UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def normalize_finding_record(item: Any) -> dict[str, Any]:
    """Normalize a domain Finding object or Mapping into a flat finding record."""
    if isinstance(item, Mapping):
        raw_id = item.get("finding_id") if item.get("finding_id") is not None else item.get("id")
        raw_severity = item.get("severity")
        raw_category = item.get("category")
        raw_has_evidence = item.get("has_evidence")
        has_evidence_attr = "evidence" in item
        raw_evidence = item.get("evidence")
        has_unproven_attr = "unproven" in item
        raw_unproven = item.get("unproven")
        raw_resolution = item.get("resolution")
        raw_reason = item.get("resolution_reason")
    else:
        raw_id = getattr(item, "finding_id", None)
        if raw_id is None:
            raw_id = getattr(item, "id", None)
        raw_severity = getattr(item, "severity", None)
        raw_category = getattr(item, "category", None)
        raw_has_evidence = getattr(item, "has_evidence", None)
        has_evidence_attr = hasattr(item, "evidence")
        raw_evidence = getattr(item, "evidence", None)
        has_unproven_attr = hasattr(item, "unproven")
        raw_unproven = getattr(item, "unproven", None)
        raw_resolution = getattr(item, "resolution", None)
        raw_reason = getattr(item, "resolution_reason", None)

    finding_id = str(raw_id or "").strip()[:64]
    if not finding_id:
        raise ValueError("finding requires a non-empty 'finding_id' or 'id'")

    severity = str(raw_severity or "unproven").strip().lower()[:16] or "unproven"
    category = str(raw_category or "bug").strip().lower()[:16] or "bug"

    if raw_has_evidence is not None:
        has_evidence = bool(raw_has_evidence)
    elif has_evidence_attr:
        has_evidence = raw_evidence is not None
    elif has_unproven_attr and raw_unproven is not None:
        has_evidence = not bool(raw_unproven)
    else:
        has_evidence = severity != "unproven"

    resolution: str | None = None
    if raw_resolution is not None:
        cleaned_res = str(raw_resolution).strip().lower()[:16]
        resolution = cleaned_res or None

    resolution_reason: str | None = None
    if raw_reason is not None:
        resolution_reason = str(raw_reason)[:1000]

    return {
        "finding_id": finding_id,
        "severity": severity,
        "category": category,
        "has_evidence": has_evidence,
        "resolution": resolution,
        "resolution_reason": resolution_reason,
    }


def compute_pair_metrics(
    reviews: Sequence[Mapping[str, Any]],
    findings_by_review: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Aggregate exact verification metrics per (author_provider, author_model, reviewer_provider, reviewer_model)."""
    grouped: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
    for review in reviews:
        verdict = str(review.get("verdict") or "").strip().lower()
        if verdict == "skipped":
            continue
        rev_provider = review.get("reviewer_provider")
        rev_model = review.get("reviewer_model")
        if not rev_provider or not rev_model:
            continue
        key = (
            str(review.get("author_provider") or ""),
            str(review.get("author_model") or ""),
            str(rev_provider),
            str(rev_model),
        )
        grouped.setdefault(key, []).append(review)

    results: list[dict[str, Any]] = []
    for (author_provider, author_model, reviewer_provider, reviewer_model), pair_reviews in grouped.items():
        reviews_count = len(pair_reviews)
        pass_count = 0
        fail_count = 0
        inconclusive_count = 0
        total_lines = 0
        total_tokens = 0
        reported_costs: list[Decimal] = []

        total_findings = 0
        proven_findings = 0
        unproven_findings = 0
        confirmed_count = 0
        fixed_count = 0
        rejected_count = 0
        open_count = 0

        for review in pair_reviews:
            verdict = str(review.get("verdict") or "").strip().lower()
            if verdict == "pass":
                pass_count += 1
            elif verdict == "fail":
                fail_count += 1
            elif verdict == "inconclusive":
                inconclusive_count += 1

            total_lines += max(0, int(review.get("lines_changed") or 0))
            total_tokens += max(0, int(review.get("input_tokens") or 0)) + max(0, int(review.get("output_tokens") or 0))

            raw_cost = review.get("cost_usd")
            if raw_cost is not None:
                reported_costs.append(Decimal(str(raw_cost)))

            review_id = str(review.get("review_id") or "")
            for finding in findings_by_review.get(review_id, ()):
                total_findings += 1
                if bool(finding.get("has_evidence")):
                    proven_findings += 1
                else:
                    unproven_findings += 1

                raw_res = finding.get("resolution")
                res = str(raw_res).strip().lower() if raw_res is not None else ""
                if res == "confirmed":
                    confirmed_count += 1
                elif res == "fixed":
                    fixed_count += 1
                elif res == "rejected":
                    rejected_count += 1
                else:
                    open_count += 1

        resolved_count = confirmed_count + fixed_count + rejected_count
        accepted_count = confirmed_count + fixed_count
        if resolved_count > 0:
            confirmed_rate: float | None = accepted_count / resolved_count
            accepted_rate: float | None = accepted_count / resolved_count
            rejected_rate: float | None = rejected_count / resolved_count
        else:
            confirmed_rate = None
            accepted_rate = None
            rejected_rate = None

        findings_per_100_lines: float | None = (
            (total_findings / total_lines) * 100.0 if total_lines > 0 else None
        )
        avg_tokens = total_tokens / reviews_count if reviews_count > 0 else 0.0

        if reported_costs:
            total_cost_dec = sum(reported_costs, Decimal("0"))
            total_cost_usd: float | None = float(total_cost_dec)
            avg_cost_usd: float | None = float(total_cost_dec / Decimal(len(reported_costs)))
        else:
            total_cost_usd = None
            avg_cost_usd = None

        results.append(
            {
                "author_provider": author_provider,
                "author_model": author_model,
                "reviewer_provider": reviewer_provider,
                "reviewer_model": reviewer_model,
                "reviews_count": reviews_count,
                "pass_count": pass_count,
                "fail_count": fail_count,
                "inconclusive_count": inconclusive_count,
                "total_findings": total_findings,
                "proven_findings": proven_findings,
                "unproven_findings": unproven_findings,
                "confirmed_count": confirmed_count,
                "fixed_count": fixed_count,
                "rejected_count": rejected_count,
                "open_count": open_count,
                "confirmed_rate": confirmed_rate,
                "accepted_rate": accepted_rate,
                "rejected_rate": rejected_rate,
                "findings_per_100_lines": findings_per_100_lines,
                "total_tokens": total_tokens,
                "avg_tokens": avg_tokens,
                "total_cost_usd": total_cost_usd,
                "avg_cost_usd": avg_cost_usd,
            }
        )

    results.sort(
        key=lambda item: (
            -int(item["reviews_count"]),
            str(item["author_provider"]),
            str(item["author_model"]),
            str(item["reviewer_provider"]),
            str(item["reviewer_model"]),
        )
    )
    return results


@runtime_checkable
class ReviewRecorder(Protocol):
    """Port used by the verification gate to persist reviews, findings, and resolutions."""

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
        files_changed: int = 0,
        lines_changed: int = 0,
        author_provider: str = "",
        author_model: str = "",
        author_effort: str | None = None,
        reviewer_provider: str | None = None,
        reviewer_model: str | None = None,
        reviewer_effort: str | None = None,
        policy: str = "cross_family",
        fallback_step: int = 1,
        started_at: datetime | None = None,
    ) -> str: ...

    def add_findings(self, review_id: str, findings: Sequence[Any]) -> None: ...

    def set_resolution(
        self,
        review_id: str,
        finding_id: str,
        resolution: str,
        reason: str | None = None,
    ) -> None: ...

    def finish(
        self,
        review_id: str,
        *,
        verdict: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | Decimal | None = None,
        finished_at: datetime | None = None,
    ) -> None: ...

    def pair_metrics(
        self,
        user_id: str,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]: ...


class InMemoryReviewRecorder:
    """Thread-safe in-memory ReviewRecorder for tests and ephemeral sessions."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._reviews: dict[str, dict[str, Any]] = {}
        self._findings: dict[str, dict[str, dict[str, Any]]] = {}

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
        files_changed: int = 0,
        lines_changed: int = 0,
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
        record: dict[str, Any] = {
            "review_id": rid,
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
            "turn_id": str(turn_id),
            "agent_id": str(agent_id or ""),
            "round": int(round),
            "trigger_reason": str(trigger_reason)[:32],
            "files_changed": max(0, int(files_changed)),
            "lines_changed": max(0, int(lines_changed)),
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
        with self._lock:
            self._reviews[rid] = record
            self._findings.setdefault(rid, {})
        return rid

    start_review = start

    def add_findings(self, review_id: str, findings: Sequence[Any]) -> None:
        rid = str(review_id)
        normalized = [normalize_finding_record(item) for item in findings]
        if not normalized:
            return
        with self._lock:
            bucket = self._findings.setdefault(rid, {})
            for rec in normalized:
                bucket[rec["finding_id"]] = {"review_id": rid, **rec}

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
        with self._lock:
            bucket = self._findings.get(rid)
            if bucket is None or fid not in bucket:
                return
            bucket[fid]["resolution"] = res
            bucket[fid]["resolution_reason"] = reason_val

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
        cost_val = Decimal(str(cost_usd)) if cost_usd is not None else None
        with self._lock:
            record = self._reviews.get(rid)
            if record is None:
                return
            record["verdict"] = str(verdict).strip().lower()[:16]
            record["input_tokens"] = max(0, int(input_tokens or 0))
            record["output_tokens"] = max(0, int(output_tokens or 0))
            record["cost_usd"] = cost_val
            record["finished_at"] = ts

    finish_review = finish

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        rid = str(review_id)
        with self._lock:
            record = self._reviews.get(rid)
            if record is None:
                return None
            findings = [dict(f) for _, f in sorted(self._findings.get(rid, {}).items())]
            return self._serialize_review(record, findings)

    def list_reviews(
        self,
        user_id: str,
        conversation_id: str | None = None,
        turn_id: str | None = None,
    ) -> list[dict[str, Any]]:
        uid = str(user_id)
        with self._lock:
            matched: list[dict[str, Any]] = []
            for rid, rec in self._reviews.items():
                if rec["user_id"] != uid:
                    continue
                if conversation_id is not None and rec["conversation_id"] != str(conversation_id):
                    continue
                if turn_id is not None and rec["turn_id"] != str(turn_id):
                    continue
                matched.append(rec)
            matched.sort(key=lambda r: (r["started_at"], r["review_id"]))
            return [
                self._serialize_review(
                    rec,
                    [dict(f) for _, f in sorted(self._findings.get(rec["review_id"], {}).items())],
                )
                for rec in matched
            ]

    def pair_metrics(
        self,
        user_id: str,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        uid = str(user_id)
        since_utc = as_utc(since)
        with self._lock:
            matched_reviews: list[dict[str, Any]] = []
            findings_by_review: dict[str, list[dict[str, Any]]] = {}
            for rid, rec in self._reviews.items():
                if rec["user_id"] != uid:
                    continue
                if since_utc is not None:
                    started = as_utc(rec["started_at"])
                    if started is None or started < since_utc:
                        continue
                matched_reviews.append(dict(rec))
                findings_by_review[rid] = [dict(f) for f in self._findings.get(rid, {}).values()]
        return compute_pair_metrics(matched_reviews, findings_by_review)

    @staticmethod
    def _serialize_review(
        record: Mapping[str, Any],
        findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        out = dict(record)
        if out.get("cost_usd") is not None:
            out["cost_usd"] = float(out["cost_usd"])
        out["findings"] = findings
        return out


__all__ = [
    "InMemoryReviewRecorder",
    "ReviewRecorder",
    "as_utc",
    "compute_pair_metrics",
    "normalize_finding_record",
]
