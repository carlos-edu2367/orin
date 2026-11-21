from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from agentos.persistence.postgres.context_observations import ContextObservationStore
from agentos.persistence.postgres.migrate import upgrade


def _make_store(tmp_path: Path, now: datetime) -> tuple[ContextObservationStore, object]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'obs.db').as_posix()}")
    upgrade(engine, "head")
    store = ContextObservationStore(engine, clock=lambda: now)
    return store, engine


def test_learn_get_list_and_upsert_clears_invalidation(tmp_path: Path) -> None:
    t0 = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
    store, engine = _make_store(tmp_path, t0)
    try:
        assert store.get("u1", "anthropic", "claude-fable-5-2") is None
        assert store.list("u1") == []

        learned = store.learn(
            "u1",
            "anthropic",
            "claude-fable-5-2",
            200_000,
            "error_raw",
            first_message_tokens=205_123,
            cli_version="2.1.309",
            account_credits_on=False,
        )
        assert learned.observed_limit_tokens == 200_000
        assert learned.source == "error_raw"
        assert learned.first_message_tokens == 205_123
        assert learned.cli_version == "2.1.309"
        assert learned.account_credits_on is False
        assert learned.invalidated_at is None

        fetched = store.get("u1", "anthropic", "claude-fable-5-2")
        assert fetched is not None
        assert fetched.observed_limit_tokens == 200_000
        assert fetched.source == "error_raw"
        assert len(store.list("u1")) == 1
        assert store.list("u2") == []

        assert store.invalidate("u1", "anthropic", "claude-fable-5-2", now=t0 + timedelta(minutes=5)) is True
        assert store.get("u1", "anthropic", "claude-fable-5-2") is None
        assert store.list("u1") == []
        assert store.invalidate("u1", "anthropic", "claude-fable-5-2") is False

        # Re-learning must clear invalidated_at and update limit/source
        relearned = store.learn(
            "u1",
            "anthropic",
            "claude-fable-5-2",
            180_000,
            "estimate",
            first_message_tokens=200_000,
            cli_version="2.1.310",
            account_credits_on=False,
            now=t0 + timedelta(minutes=10),
        )
        assert relearned.invalidated_at is None
        assert relearned.observed_limit_tokens == 180_000
        assert relearned.source == "estimate"
        active = store.get("u1", "anthropic", "claude-fable-5-2")
        assert active is not None
        assert active.observed_limit_tokens == 180_000
    finally:
        engine.dispose()


def test_invalidate_if_environment_changed_checks_credits_and_cli_version(tmp_path: Path) -> None:
    t0 = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
    store, engine = _make_store(tmp_path, t0)
    try:
        store.learn(
            "u1",
            "anthropic",
            "claude-fable-5-2",
            200_000,
            "error_raw",
            cli_version="2.1.309",
            account_credits_on=False,
        )

        # Unchanged environment -> stays active
        assert (
            store.invalidate_if_environment_changed(
                "u1", "anthropic", "claude-fable-5-2", cli_version="2.1.309", account_credits_on=False
            )
            is False
        )
        assert store.get("u1", "anthropic", "claude-fable-5-2") is not None

        # Unknown probes (None) -> do not spuriously invalidate
        assert (
            store.invalidate_if_environment_changed(
                "u1", "anthropic", "claude-fable-5-2", cli_version=None, account_credits_on=None
            )
            is False
        )
        assert store.get("u1", "anthropic", "claude-fable-5-2") is not None

        # Credits changed False -> True -> invalidates!
        assert (
            store.invalidate_if_environment_changed(
                "u1", "anthropic", "claude-fable-5-2", cli_version="2.1.309", account_credits_on=True
            )
            is True
        )
        assert store.get("u1", "anthropic", "claude-fable-5-2") is None

        # Re-learn, then change CLI version -> invalidates!
        store.learn(
            "u1",
            "anthropic",
            "claude-fable-5-2",
            200_000,
            "error_raw",
            cli_version="2.1.309",
            account_credits_on=False,
        )
        assert (
            store.invalidate_if_environment_changed(
                "u1", "anthropic", "claude-fable-5-2", cli_version="2.1.310", account_credits_on=False
            )
            is True
        )
        assert store.get("u1", "anthropic", "claude-fable-5-2") is None
    finally:
        engine.dispose()


def test_learn_rejects_invalid_limit_or_source(tmp_path: Path) -> None:
    t0 = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
    store, engine = _make_store(tmp_path, t0)
    try:
        with pytest.raises(ValueError):
            store.learn("u1", "anthropic", "claude-fable-5-2", 0, "error_raw")
        with pytest.raises(ValueError):
            store.learn("u1", "anthropic", "claude-fable-5-2", 200_000, "invalid_source")
    finally:
        engine.dispose()
