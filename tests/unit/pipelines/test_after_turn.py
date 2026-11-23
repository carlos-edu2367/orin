"""WS-C7: the one hook that runs after a turn ends (``/goal`` and ``RunPipeline`` plug in here, later)."""

from __future__ import annotations

import logging

import pytest

from agentos.pipelines import after_turn


@pytest.fixture(autouse=True)
def _clean():
    after_turn.clear_handlers()
    yield
    after_turn.clear_handlers()


TURN = {"turn_id": "t1", "conversation_id": "c1", "user_id": "u1", "turn_kind": None}


def test_without_a_handler_it_is_a_no_op() -> None:
    assert after_turn.run_after_turn(None, TURN, "completed") == 0


def test_a_completed_turn_calls_every_handler_in_order() -> None:
    seen: list[tuple[str, str]] = []
    after_turn.register(lambda store, turn, state: seen.append(("a", state)))
    after_turn.register(lambda store, turn, state: seen.append(("b", state)))
    assert after_turn.run_after_turn("store", TURN, "completed") == 2
    assert seen == [("a", "completed"), ("b", "completed")]
    after_turn.run_after_turn("store", TURN, "completed_with_caveats")
    assert seen[-1] == ("b", "completed_with_caveats")


@pytest.mark.parametrize("state", ["failed", "cancelled", "waiting_user", "reconciliation_required"])
def test_a_turn_that_did_not_complete_calls_nothing(state: str) -> None:
    seen: list[str] = []
    after_turn.register(lambda store, turn, result_state: seen.append(result_state))
    assert after_turn.run_after_turn("store", TURN, state) == 0
    assert seen == []


def test_a_handler_that_raises_is_logged_and_swallowed(caplog) -> None:
    seen: list[str] = []

    def broken(store, turn, state) -> None:
        raise RuntimeError("boom")

    after_turn.register(broken)
    after_turn.register(lambda store, turn, state: seen.append("after"))
    with caplog.at_level(logging.ERROR, logger="agentos.pipelines.after_turn"):
        assert after_turn.run_after_turn("store", TURN, "completed") == 1
    assert seen == ["after"]
    assert any("boom" in (record.exc_text or "") or "after-turn" in record.getMessage() for record in caplog.records)


def test_registering_the_same_handler_twice_keeps_one_copy() -> None:
    seen: list[int] = []

    def handler(store, turn, state) -> None:
        seen.append(1)

    after_turn.register(handler)
    after_turn.register(handler)
    after_turn.run_after_turn("store", TURN, "completed")
    assert seen == [1]


# ------------------------------------------------------------------------ through the real worker
def _worker_turn(result_state: str, *, raises: bool = False):
    from sqlalchemy import create_engine

    from agentos.agentic.runtime import AgenticRunResult
    from agentos.conversations.chat import PostgresChatStore
    from agentos.persistence.postgres.schema import metadata
    from agentos.workers.chat import ChatWorker

    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(user_id="user-1", message="oi", provider="openrouter", model_id="model-1", idempotency_key=f"k-{result_state}")

    class Runtime:
        def run(self, turn_id):  # noqa: ANN001, ANN201
            return AgenticRunResult(result_state)

        def close(self):  # noqa: ANN201
            return None

    worker = ChatWorker(store)
    worker._runtime_for = lambda turn: Runtime()  # type: ignore[method-assign]
    order: list[str] = []
    original_promote = worker._promote_queued_inputs

    def promote(turn, state):  # noqa: ANN001, ANN201
        order.append("promote")
        return original_promote(turn, state)

    worker._promote_queued_inputs = promote  # type: ignore[method-assign]

    def handler(seen_store, turn, state):  # noqa: ANN001, ANN201
        order.append(f"hook:{state}:{turn['turn_id'] == receipt.turn_id}:{seen_store is store}")
        if raises:
            raise RuntimeError("goal evaluator is down")

    after_turn.register(handler)
    worker.run(receipt.turn_id)
    return order


def test_the_worker_calls_the_hook_once_after_it_promotes_the_queue_for_a_completed_turn() -> None:
    assert _worker_turn("completed") == ["promote", "hook:completed:True:True"]


def test_the_worker_does_not_call_the_hook_for_a_failed_turn() -> None:
    assert _worker_turn("failed") == ["promote"]


def test_a_hook_that_raises_never_breaks_the_worker() -> None:
    assert _worker_turn("completed", raises=True) == ["promote", "hook:completed:True:True"]
