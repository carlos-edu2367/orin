from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, insert

from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.quota import QuotaSnapshotStore
from agentos.persistence.postgres.schema import conversation_agent_usage
from agentos.quota.models import QuotaSnapshot


def _make_store(tmp_path: Path) -> tuple[QuotaSnapshotStore, object]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'quota store ação.db').as_posix()}")
    upgrade(engine, "head")
    return QuotaSnapshotStore(engine), engine


def test_quota_store_add_latest_and_prune_14_days(tmp_path: Path):
    store, engine = _make_store(tmp_path)
    try:
        now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
        old_snap = QuotaSnapshot(
            provider="anthropic-cli",
            window_kind="five_hour",
            used_percent=20.0,
            status="allowed",
            resets_at=now - timedelta(days=15),
            source="rate_limit_event",
            observed_at=now - timedelta(days=15),
        )
        recent_snap_1 = QuotaSnapshot(
            provider="anthropic-cli",
            window_kind="five_hour",
            used_percent=45.0,
            status="allowed",
            resets_at=now + timedelta(hours=3),
            source="rate_limit_event",
            observed_at=now - timedelta(minutes=10),
        )
        recent_snap_2 = QuotaSnapshot(
            provider="anthropic-cli",
            window_kind="seven_day",
            used_percent=77.0,
            status="allowed_warning",
            resets_at=now + timedelta(days=4),
            source="claude_usage_cache",
            observed_at=now - timedelta(minutes=5),
        )
        scoped_snap = QuotaSnapshot(
            provider="anthropic-cli",
            window_kind="weekly_scoped",
            used_percent=0.0,
            status="allowed",
            resets_at=now + timedelta(days=4),
            source="claude_usage_cache",
            observed_at=now - timedelta(minutes=5),
            scope_model="Fable",
        )
        codex_snap = QuotaSnapshot(
            provider="openai-codex",
            window_kind="five_hour",
            used_percent=30.0,
            status="allowed",
            resets_at=now + timedelta(hours=4),
            source="codex_rollout",
            observed_at=now - timedelta(minutes=2),
        )

        store.add("user-1", old_snap)
        store.add_many("user-1", [recent_snap_1, recent_snap_2, scoped_snap, codex_snap])

        # latest returns most recent snapshot per (provider, window_kind, scope_model)
        claude_latest = store.latest("user-1", provider="anthropic-cli")
        by_key = {(s.window_kind, s.scope_model): s for s in claude_latest}
        assert by_key[("five_hour", None)].used_percent == 45.0
        assert by_key[("seven_day", None)].used_percent == 77.0
        assert by_key[("weekly_scoped", "Fable")].used_percent == 0.0

        all_latest = store.latest("user-1")
        assert {s.provider for s in all_latest} == {"anthropic-cli", "openai-codex"}

        # Prune older than 14 days deletes only old_snap
        deleted = store.prune(now - timedelta(days=14))
        assert deleted == 1

        remaining = store.latest("user-1", provider="anthropic-cli")
        assert len(remaining) == 3
    finally:
        engine.dispose()


def test_quota_store_orin_usage_aggregates_5h_and_7d(tmp_path: Path):
    store, engine = _make_store(tmp_path)
    try:
        now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
        with engine.begin() as conn:
            conn.execute(
                insert(conversation_agent_usage),
                [
                    {
                        "conversation_id": "c1",
                        "agent_id": "a1",
                        "user_id": "user-1",
                        "provider": "anthropic",
                        "model_id": "claude-sonnet-5-5",
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "total_tokens": 1500,
                        "usage_reported": True,
                        "updated_at": now - timedelta(hours=1),
                    },
                    {
                        "conversation_id": "c2",
                        "agent_id": "a2",
                        "user_id": "user-1",
                        "provider": "anthropic-cli",
                        "model_id": "claude-sonnet-5-5",
                        "input_tokens": 2000,
                        "output_tokens": 1000,
                        "total_tokens": 3000,
                        "usage_reported": True,
                        "updated_at": now - timedelta(days=2),
                    },
                    {
                        "conversation_id": "c3",
                        "agent_id": "a3",
                        "user_id": "user-1",
                        "provider": "anthropic",
                        "model_id": "claude-sonnet-5-5",
                        "input_tokens": 9000,
                        "output_tokens": 1000,
                        "total_tokens": 10000,
                        "usage_reported": True,
                        "updated_at": now - timedelta(days=10),
                    },
                    {
                        "conversation_id": "c4",
                        "agent_id": "a4",
                        "user_id": "other-user",
                        "provider": "anthropic",
                        "model_id": "claude-sonnet-5-5",
                        "input_tokens": 5000,
                        "output_tokens": 5000,
                        "total_tokens": 10000,
                        "usage_reported": True,
                        "updated_at": now - timedelta(minutes=30),
                    },
                ],
            )

        summary = store.orin_usage("user-1", "anthropic-cli", now=now)
        assert summary.five_hour.tokens == 1500
        assert summary.five_hour.calls == 1
        assert summary.seven_day.tokens == 4500
        assert summary.seven_day.calls == 2
    finally:
        engine.dispose()
