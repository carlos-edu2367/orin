from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.api.contracts import ApplicationNotFoundError, ApplicationValidationError
from agentos.conversations.chat import MAX_QUEUED_INPUTS, ChatApplication, PostgresChatStore
from agentos.persistence.postgres.schema import conversation_messages, conversation_turns, metadata


def _store() -> PostgresChatStore:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def _running_turn(store: PostgresChatStore) -> dict:
    receipt = store.create(user_id="u", message="first", provider="openrouter", model_id="m", idempotency_key="k1")
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    return turn


def test_enqueue_list_cancel_and_claim() -> None:
    store = _store()
    turn = _running_turn(store)
    conversation_id = str(turn["conversation_id"])
    first = store.enqueue_input(user_id="u", conversation_id=conversation_id, content="  also do X  ")
    second = store.enqueue_input(user_id="u", conversation_id=conversation_id, content="and Y")
    assert first["content"] == "also do X"
    assert [item["content"] for item in store.queued_inputs(conversation_id, "u")] == ["also do X", "and Y"]
    assert store.cancel_queued_input(conversation_id, "u", str(second["queue_id"])) is True
    assert store.cancel_queued_input(conversation_id, "u", str(second["queue_id"])) is False
    claimed = store.claim_queued_inputs(turn)
    assert [item["content"] for item in claimed] == ["also do X"]
    assert store.claim_queued_inputs(turn) == []
    assert store.queued_inputs(conversation_id, "u") == []


def test_the_queue_is_owned_bounded_and_validated() -> None:
    store = _store()
    turn = _running_turn(store)
    conversation_id = str(turn["conversation_id"])
    with pytest.raises(ApplicationNotFoundError):
        store.enqueue_input(user_id="someone-else", conversation_id=conversation_id, content="x")
    with pytest.raises(ApplicationNotFoundError):
        store.queued_inputs(conversation_id, "someone-else")
    with pytest.raises(ValueError):
        store.enqueue_input(user_id="u", conversation_id=conversation_id, content="   ")
    for index in range(MAX_QUEUED_INPUTS):
        store.enqueue_input(user_id="u", conversation_id=conversation_id, content=f"item {index}")
    with pytest.raises(ApplicationValidationError):
        store.enqueue_input(user_id="u", conversation_id=conversation_id, content="one too many")


def test_take_and_restore_never_lose_text() -> None:
    store = _store()
    turn = _running_turn(store)
    conversation_id = str(turn["conversation_id"])
    store.enqueue_input(user_id="u", conversation_id=conversation_id, content="a")
    store.enqueue_input(user_id="u", conversation_id=conversation_id, content="b")
    taken = store.take_queued_inputs(turn)
    assert taken is not None and taken[0] == "a\n\nb" and len(taken[1]) == 2
    store.restore_queued_inputs(taken[1])
    assert [item["content"] for item in store.queued_inputs(conversation_id, "u")] == ["a", "b"]


def test_enqueue_without_a_live_turn_sends_immediately() -> None:
    store = _store()
    turn = _running_turn(store)
    conversation_id = str(turn["conversation_id"])
    store.finish(turn)
    app = ChatApplication(store)
    result = app.enqueue("u", conversation_id, "follow up", "k2")
    assert result["queued"] is False and result["turn_id"]
    assert store.queued_inputs(conversation_id, "u") == []


def test_input_left_in_the_queue_becomes_the_next_turn() -> None:
    store = _store()
    turn = _running_turn(store)
    conversation_id = str(turn["conversation_id"])
    app = ChatApplication(store)
    assert app.enqueue("u", conversation_id, "one", "k2")["queued"] is True
    assert app.enqueue("u", conversation_id, "two", "k3")["queued"] is True
    store.finish(turn)
    receipt = app.promote_queued_inputs(conversation_id, "u", source_turn_id=str(turn["turn_id"]))
    assert receipt is not None
    with store._engine.connect() as connection:
        user_message = connection.execute(select(conversation_messages.c.content).join(
            conversation_turns, conversation_turns.c.user_message_id == conversation_messages.c.message_id,
        ).where(conversation_turns.c.turn_id == receipt.turn_id)).scalar_one()
    assert user_message == "one\n\ntwo"
    assert app.promote_queued_inputs(conversation_id, "u") is None


def _turn_count(store: PostgresChatStore, conversation_id: str) -> int:
    with store._engine.connect() as connection:
        return len(connection.execute(select(conversation_turns.c.turn_id).where(conversation_turns.c.conversation_id == conversation_id)).all())


def test_queued_text_never_answers_a_question() -> None:
    from agentos.workers.chat import ChatWorker

    store = _store()
    turn = _running_turn(store)
    conversation_id = str(turn["conversation_id"])
    assert ChatApplication(store).enqueue("u", conversation_id, "also update the README", "k2")["queued"] is True
    store.delta(turn, "Before I continue: should I DROP the legacy_invoices table? (yes/no)")
    store.finish(turn, code="WAITING_USER")
    ChatWorker(store)._promote_queued_inputs(dict(turn), "waiting_user")
    # The question stays open for the person; the queued text is still theirs to send or delete.
    assert [item["content"] for item in store.queued_inputs(conversation_id, "u")] == ["also update the README"]
    assert _turn_count(store, conversation_id) == 1


def test_queued_text_waits_out_a_safety_pause() -> None:
    from agentos.workers.chat import ChatWorker

    store = _store()
    turn = _running_turn(store)
    conversation_id = str(turn["conversation_id"])
    assert ChatApplication(store).enqueue("u", conversation_id, "keep going", "k2")["queued"] is True
    store.finish(turn, code="SAFETY_TOOL_ROUNDS")
    ChatWorker(store)._promote_queued_inputs(dict(turn), "waiting_user")
    assert [item["content"] for item in store.queued_inputs(conversation_id, "u")] == ["keep going"]
    assert _turn_count(store, conversation_id) == 1


@pytest.mark.parametrize("result_state", ["completed", "completed_with_caveats"])
def test_queued_text_is_promoted_after_a_finished_turn(result_state: str) -> None:
    from agentos.workers.chat import ChatWorker

    store = _store()
    turn = _running_turn(store)
    conversation_id = str(turn["conversation_id"])
    assert ChatApplication(store).enqueue("u", conversation_id, "also do Y", "k2")["queued"] is True
    store.finish(turn)
    ChatWorker(store)._promote_queued_inputs(dict(turn), result_state)
    assert store.queued_inputs(conversation_id, "u") == []
    assert _turn_count(store, conversation_id) == 2


def test_a_delivered_queued_message_stays_in_the_conversation() -> None:
    """A queued message the model received mid-turn is in every later turn's history, whatever the budget."""
    store = _store()
    app = ChatApplication(store)
    receipt = store.create(user_id="u", message="migrate the db layer", provider="openrouter", model_id="m", idempotency_key="k1")
    turn = store.claim(receipt.turn_id)
    assert turn is not None

    class Adapter:  # what the worker's runtime store forwards
        def load(self, turn_id): return dict(turn)
        def history_for_turn(self, t): return store.history_for_turn(t)
        def lifecycle(self, t, state, **payload): pass
        def delta(self, t, text): store.delta(t, text)
        def finish(self, t, *, failed=False, code=None): store.finish(t, failed=failed, code=code)
        def record_step(self, t, **values): store.record_step(t, **values)
        def claim_queued_inputs(self, t): return store.claim_queued_inputs(t)

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                app.enqueue("u", receipt.conversation_id, "IMPORTANT: never touch payments/legacy.py", "k-q")
            if self.calls <= 3:
                return iter([
                    NormalizedStreamItem(StreamKind.TOOL_CALL, 1, tool_call_id=f"c{self.calls}", tool_name="read_file", arguments_delta='{"path": "a"}'),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
                ])
            return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text="ok, done"), NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")])

    class Toolset:
        def schemas(self, allowed=None): return []
        def is_read_only(self, name): return True
        def argument_names(self, name): return frozenset({"path"})
        def invoke(self, name, arguments): return ToolOutcome("succeeded", "ok", "file content " + "x" * 4000)

    runtime = AgenticTurnRuntime(
        store=Adapter(), provider=Provider(), toolset=Toolset(),
        limits=AgenticLimits(max_iterations=None, max_actions=None, deadline=timedelta(hours=1)),
    )
    assert runtime.run(str(turn["turn_id"])).queued_turn_count == 1

    next_turn = store.claim(app.send("u", receipt.conversation_id, "now add the tests", "k2").turn_id)
    assert next_turn is not None
    for budget in (0, 1_500, 100_000):
        text = "\n".join(str(message.get("content")) for message in store.history_for_turn(next_turn, rehydration_budget_tokens=budget))
        assert text.count("never touch payments") == 1, f"budget {budget}"


def test_a_safety_pause_is_stored_as_waiting_for_the_person() -> None:
    """The stored chat turn reads waiting_user after the safety pause, as it does after ask_user."""
    store = _store()
    receipt = store.create(user_id="u", message="big job", provider="openrouter", model_id="m", idempotency_key="k9")
    running = store.claim(receipt.turn_id)
    assert running is not None

    class Adapter:  # what the worker's runtime store forwards
        def load(self, turn_id): return dict(running)
        def history_for_turn(self, t): return store.history_for_turn(t)
        def lifecycle(self, t, state, **payload): pass
        def delta(self, t, text): store.delta(t, text)
        def finish(self, t, *, failed=False, code=None): store.finish(t, failed=failed, code=code)

    class Provider:
        def stream(self, request):
            return iter([
                NormalizedStreamItem(StreamKind.TOOL_CALL, 1, tool_call_id="c1", tool_name="read_file", arguments_delta='{"path": "a"}'),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
            ])

    class Toolset:
        def schemas(self, allowed=None): return []
        def is_read_only(self, name): return True
        def argument_names(self, name): return frozenset({"path"})
        def invoke(self, name, arguments): return ToolOutcome("succeeded", "ok", "content")

    result = AgenticTurnRuntime(
        store=Adapter(), provider=Provider(), toolset=Toolset(),
        limits=AgenticLimits(max_iterations=None, max_actions=None, deadline=timedelta(hours=1), safety_max_tool_rounds=2),
    ).run(str(running["turn_id"]))
    assert (result.state, result.error_code) == ("waiting_user", "SAFETY_TOOL_ROUNDS")
    with store._engine.connect() as connection:
        state = connection.execute(select(conversation_turns.c.state).where(conversation_turns.c.turn_id == running["turn_id"])).scalar_one()
    assert state == "waiting_user"


def test_a_repeated_idempotency_key_returns_the_first_queue_row() -> None:
    """W3-R20: a retried queue POST with the same Idempotency-Key queues its text once."""
    store = _store()
    turn = _running_turn(store)
    conversation_id = str(turn["conversation_id"])
    app = ChatApplication(store)
    first = app.enqueue("u", conversation_id, "also do X", "same-key")
    second = app.enqueue("u", conversation_id, "also do X", "same-key")
    assert second["queue_id"] == first["queue_id"]
    assert [item["content"] for item in store.queued_inputs(conversation_id, "u")] == ["also do X"]


def test_a_retry_after_the_turn_ended_neither_queues_nor_sends_again() -> None:
    store = _store()
    turn = _running_turn(store)
    conversation_id = str(turn["conversation_id"])
    app = ChatApplication(store)
    first = app.enqueue("u", conversation_id, "also do X", "same-key")
    store.finish(turn)
    assert app.promote_queued_inputs(conversation_id, "u") is not None
    assert _turn_count(store, conversation_id) == 2
    again = app.enqueue("u", conversation_id, "also do X", "same-key")
    assert again["queue_id"] == first["queue_id"]
    assert _turn_count(store, conversation_id) == 2


def test_the_store_returns_the_row_of_a_repeated_key() -> None:
    store = _store()
    turn = _running_turn(store)
    conversation_id = str(turn["conversation_id"])
    first = store.enqueue_input(user_id="u", conversation_id=conversation_id, content="a", idempotency_key="k")
    again = store.enqueue_input(user_id="u", conversation_id=conversation_id, content="a", idempotency_key="k")
    assert again["queue_id"] == first["queue_id"]
    assert len(store.queued_inputs(conversation_id, "u")) == 1
