"""Durable store for subscription quota snapshots and Orin usage aggregation."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, desc, func, insert, select
from sqlalchemy.engine import Engine

from agentos.quota.models import OrinUsageSummary, OrinUsageWindow, QuotaSnapshot
from agentos.quota.normalize import normalize_provider_name

from .schema import conversation_agent_usage, subscription_quota_snapshots

_PROVIDER_USAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "anthropic-cli": ("anthropic-cli", "anthropic", "claude-cli", "claude_cli"),
    "openai-codex": ("openai-codex", "codex", "codex-cli", "codex_cli"),
    "google-antigravity": ("google-antigravity", "antigravity", "antigravity-cli", "agy"),
}


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _row_to_snapshot(row: Any) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider=str(row["provider"]),
        window_kind=str(row["window_kind"]),
        used_percent=float(row["used_percent"]) if row["used_percent"] is not None else None,
        status=str(row["status"]),
        resets_at=_as_utc(row["resets_at"]),
        source=str(row["source"]),
        observed_at=_as_utc(row["observed_at"]) or datetime.now(UTC),
        scope_model=str(row["scope_model"]) if row["scope_model"] is not None else None,
        raw=dict(row["raw"]) if isinstance(row["raw"], dict) else None,
    )


class QuotaSnapshotStore:
    """SQLAlchemy-backed persistence for `subscription_quota_snapshots`."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, user_id: str, snapshot: QuotaSnapshot) -> None:
        self.add_many(user_id, (snapshot,))

    def add_many(self, user_id: str, snapshots: Iterable[QuotaSnapshot]) -> int:
        rows: list[dict[str, Any]] = []
        for snap in snapshots:
            rows.append(
                {
                    "user_id": str(user_id),
                    "provider": normalize_provider_name(snap.provider),
                    "window_kind": str(snap.window_kind),
                    "used_percent": snap.used_percent,
                    "status": str(snap.status),
                    "resets_at": _as_utc(snap.resets_at),
                    "source": str(snap.source),
                    "scope_model": snap.scope_model,
                    "observed_at": _as_utc(snap.observed_at) or datetime.now(UTC),
                    "raw": snap.raw,
                }
            )
        if not rows:
            return 0
        with self._engine.begin() as conn:
            conn.execute(insert(subscription_quota_snapshots), rows)
        return len(rows)

    def latest(self, user_id: str, provider: str | None = None) -> list[QuotaSnapshot]:
        """Return the latest snapshot for each `(provider, window_kind, scope_model)` tuple."""
        t = subscription_quota_snapshots
        conditions = [t.c.user_id == str(user_id)]
        if provider is not None:
            conditions.append(t.c.provider == normalize_provider_name(provider))

        stmt = (
            select(t)
            .where(and_(*conditions))
            .order_by(desc(t.c.observed_at), desc(t.c.id))
            .limit(500)
        )
        seen: set[tuple[str, str, str | None]] = set()
        out: list[QuotaSnapshot] = []
        with self._engine.connect() as conn:
            for row in conn.execute(stmt).mappings():
                key = (str(row["provider"]), str(row["window_kind"]), row["scope_model"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(_row_to_snapshot(row))
        return out

    def orin_usage(
        self,
        user_id: str,
        provider: str,
        *,
        now: datetime | None = None,
    ) -> OrinUsageSummary:
        """Aggregate Orin-tracked tokens and calls from `conversation_agent_usage` for 5h and 7d."""
        ref_now = _as_utc(now) or datetime.now(UTC)
        canon = normalize_provider_name(provider)
        aliases = _PROVIDER_USAGE_ALIASES.get(canon, (canon,))
        t = conversation_agent_usage

        def _query_window(since: datetime) -> OrinUsageWindow:
            tokens_expr = func.coalesce(
                t.c.total_tokens,
                func.coalesce(t.c.input_tokens, 0) + func.coalesce(t.c.output_tokens, 0),
            )
            stmt = select(
                func.coalesce(func.sum(tokens_expr), 0).label("tokens"),
                func.count().label("calls"),
            ).where(
                and_(
                    t.c.user_id == str(user_id),
                    t.c.provider.in_(aliases),
                    t.c.updated_at >= since,
                )
            )
            with self._engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()
            if not row:
                return OrinUsageWindow(tokens=0, calls=0)
            return OrinUsageWindow(
                tokens=int(row["tokens"] or 0),
                calls=int(row["calls"] or 0),
            )

        return OrinUsageSummary(
            five_hour=_query_window(ref_now - timedelta(hours=5)),
            seven_day=_query_window(ref_now - timedelta(days=7)),
        )

    def prune(self, older_than: datetime) -> int:
        cutoff = _as_utc(older_than) or datetime.now(UTC)
        t = subscription_quota_snapshots
        with self._engine.begin() as conn:
            result = conn.execute(delete(t).where(t.c.observed_at < cutoff))
            return int(result.rowcount or 0)


__all__ = ["QuotaSnapshotStore"]
