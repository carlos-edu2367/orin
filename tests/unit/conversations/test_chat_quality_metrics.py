from __future__ import annotations

from sqlalchemy import create_engine

from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata


def _store() -> PostgresChatStore:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def _turn(store: PostgresChatStore, key: str) -> dict[str, object]:
    receipt = store.create(
        user_id="user-1", message="Reformule o orçamento.", provider="openrouter",
        model_id="anthropic/claude-sonnet-4", idempotency_key=key,
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    return turn


def _counters(**overrides: object) -> dict[str, object]:
    row = {
        "tool_calls": 10, "redundant_tool_calls": 2, "iterations": 4,
        "input_tokens": 8000, "output_tokens": 500, "cached_input_tokens": None,
    }
    row.update(overrides)
    return row


def test_a_finished_turn_records_one_quality_row() -> None:
    store = _store()
    turn = _turn(store, "request-1")
    store.record_quality(turn, counters=_counters(), outcome="completed", error_code=None, duration_ms=1200)
    summary = store.quality_summary("user-1")
    assert len(summary) == 1
    assert summary[0]["turns"] == 1
    assert summary[0]["completed_turns"] == 1
    assert summary[0]["tool_calls"] == 10
    assert summary[0]["tool_calls_per_completed_turn"] == 10.0
    assert summary[0]["redundant_fraction"] == 0.2


def test_recording_the_same_turn_twice_keeps_one_row() -> None:
    """Recovery can drive a turn to a terminal state more than once.

    The row describes the turn, not the number of attempts.
    """
    store = _store()
    turn = _turn(store, "request-1")
    store.record_quality(turn, counters=_counters(), outcome="completed", error_code=None, duration_ms=1200)
    store.record_quality(turn, counters=_counters(tool_calls=99), outcome="failed", error_code="X", duration_ms=50)
    summary = store.quality_summary("user-1")
    assert summary[0]["turns"] == 1
    assert summary[0]["tool_calls"] == 10


def test_a_failed_turn_counts_against_the_completion_rate() -> None:
    store = _store()
    store_turn_one = _turn(store, "request-1")
    store_turn_two = _turn(store, "request-2")
    store.record_quality(store_turn_one, counters=_counters(), outcome="completed", error_code=None, duration_ms=1000)
    store.record_quality(store_turn_two, counters=_counters(), outcome="failed", error_code="ITERATION_LIMIT", duration_ms=2000)
    summary = store.quality_summary("user-1")
    assert summary[0]["turns"] == 2
    assert summary[0]["completed_turns"] == 1
    assert summary[0]["completion_rate"] == 0.5
    # Twenty calls spent, one turn actually finished.
    assert summary[0]["tool_calls_per_completed_turn"] == 20.0


def test_cached_fraction_is_none_when_no_provider_reported_cache() -> None:
    store = _store()
    turn = _turn(store, "request-1")
    store.record_quality(turn, counters=_counters(), outcome="completed", error_code=None, duration_ms=100)
    assert store.quality_summary("user-1")[0]["cached_fraction"] is None


def test_cached_fraction_is_reported_once_a_provider_measures_it() -> None:
    store = _store()
    turn = _turn(store, "request-1")
    store.record_quality(
        turn, counters=_counters(input_tokens=1000, cached_input_tokens=750),
        outcome="completed", error_code=None, duration_ms=100,
    )
    assert store.quality_summary("user-1")[0]["cached_fraction"] == 0.75


def test_a_broken_recording_never_raises() -> None:
    """Telemetry must not be able to end a turn."""
    store = _store()
    store.record_quality(
        {"turn_id": "missing", "conversation_id": "c", "user_id": "u"},
        counters={"tool_calls": "not a number"}, outcome="completed", error_code=None, duration_ms=0,
    )


def test_another_user_does_not_see_these_turns() -> None:
    store = _store()
    turn = _turn(store, "request-1")
    store.record_quality(turn, counters=_counters(), outcome="completed", error_code=None, duration_ms=100)
    assert store.quality_summary("user-2") == []
