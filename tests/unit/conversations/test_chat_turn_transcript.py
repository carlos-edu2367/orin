from __future__ import annotations

from sqlalchemy import create_engine

from agentos.agentic import transcript
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata


def _store() -> PostgresChatStore:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def _first_turn(store: PostgresChatStore, message: str = "Leia o orçamento.") -> dict[str, object]:
    receipt = store.create(
        user_id="user-1", message=message, provider="openrouter",
        model_id="anthropic/claude-sonnet-4", idempotency_key="request-1",
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    return turn


def _follow_up(
    store: PostgresChatStore,
    conversation_id: str,
    message: str = "Agora refaça com 18%.",
    *,
    key: str = "request-2",
) -> dict[str, object]:
    receipt = store.create(
        user_id="user-1", message=message, provider="openrouter",
        model_id="anthropic/claude-sonnet-4", idempotency_key=key, conversation_id=conversation_id,
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    return turn


def _record_a_read(store: PostgresChatStore, turn: dict[str, object], *, path: str, content: str) -> None:
    store.record_step(
        turn, kind=transcript.STEP_ASSISTANT_TOOL_CALL,
        payload=transcript.assistant_tool_call_payload(
            "", [{"id": "call-1", "name": "read_file", "arguments": f'{{"path": "{path}"}}'}],
        ),
    )
    store.record_step(
        turn, kind=transcript.STEP_TOOL_RESULT,
        payload=transcript.tool_result_payload(call_id="call-1", name="read_file", status="succeeded", content=content),
        tool_name="read_file", tool_call_id="call-1",
    )


def test_a_follow_up_turn_sees_what_the_previous_turn_read() -> None:
    """This is the whole point of the transcript.

    Before it existed, the second turn's history was two lines of chat and
    the agent had to read the file again to know anything about it.
    """
    store = _store()
    first = _first_turn(store)
    _record_a_read(store, first, path="orcamento.xlsx", content="Total: 45320.10")
    store.delta(first, "Li a planilha.")
    store.finish(first)

    second = _follow_up(store, str(first["conversation_id"]))
    history = store.history_for_turn(second, rehydration_budget_tokens=10_000)

    replayed = str(history)
    assert "orcamento.xlsx" in replayed
    assert "Total: 45320.10" in replayed


def test_the_replayed_call_comes_before_the_answer_it_produced() -> None:
    store = _store()
    first = _first_turn(store)
    _record_a_read(store, first, path="orcamento.xlsx", content="Total: 45320.10")
    store.delta(first, "Li a planilha.")
    store.finish(first)

    second = _follow_up(store, str(first["conversation_id"]))
    history = store.history_for_turn(second, rehydration_budget_tokens=10_000)

    roles = [item["role"] for item in history]
    tool_index = roles.index("tool")
    answer_index = next(i for i, item in enumerate(history) if item.get("content") == "Li a planilha.")
    assert tool_index < answer_index


def test_without_a_budget_the_history_is_exactly_what_it_always_was() -> None:
    """Every caller that only wants the readable chat must be unaffected."""
    store = _store()
    first = _first_turn(store)
    _record_a_read(store, first, path="orcamento.xlsx", content="Total: 45320.10")
    store.delta(first, "Li a planilha.")
    store.finish(first)

    second = _follow_up(store, str(first["conversation_id"]))
    history = store.history_for_turn(second)

    assert all(item["role"] in {"user", "assistant"} for item in history)
    assert "orcamento.xlsx" not in str(history)


def test_a_conversation_recorded_before_the_transcript_existed_still_works() -> None:
    """No backfill is possible; those turns simply have no steps."""
    store = _store()
    first = _first_turn(store)
    store.delta(first, "Respondi sem ferramentas.")
    store.finish(first)

    second = _follow_up(store, str(first["conversation_id"]))
    history = store.history_for_turn(second, rehydration_budget_tokens=10_000)

    assert [item["role"] for item in history] == ["user", "assistant", "user"]


def test_the_current_turns_own_steps_are_not_replayed_into_its_history() -> None:
    """The loop already holds them in memory; replaying would duplicate them."""
    store = _store()
    first = _first_turn(store)
    _record_a_read(store, first, path="orcamento.xlsx", content="Total: 45320.10")

    history = store.history_for_turn(first, rehydration_budget_tokens=10_000)

    assert "orcamento.xlsx" not in str(history)


def test_a_tight_budget_drops_old_work_and_keeps_recent_work() -> None:
    store = _store()
    first = _first_turn(store)
    _record_a_read(store, first, path="antigo.txt", content="A" * 12_000)
    store.delta(first, "Primeiro passo.")
    store.finish(first)

    second = _follow_up(store, str(first["conversation_id"]))
    store.record_step(
        second, kind=transcript.STEP_ASSISTANT_TOOL_CALL,
        payload=transcript.assistant_tool_call_payload("", [{"id": "call-9", "name": "read_file", "arguments": '{"path": "recente.txt"}'}]),
    )
    store.record_step(
        second, kind=transcript.STEP_TOOL_RESULT,
        payload=transcript.tool_result_payload(call_id="call-9", name="read_file", status="succeeded", content="recente"),
        tool_name="read_file", tool_call_id="call-9",
    )
    store.delta(second, "Segundo passo.")
    store.finish(second)

    third = _follow_up(store, str(first["conversation_id"]), "E agora?", key="request-3")
    history = store.history_for_turn(third, rehydration_budget_tokens=200)

    replayed = str(history)
    assert "recente.txt" in replayed
    assert "A" * 100 not in replayed


def test_a_step_written_for_a_subagent_stays_out_of_the_conversation_history() -> None:
    store = _store()
    first = _first_turn(store)
    store.record_step(
        first, kind=transcript.STEP_ASSISTANT_TOOL_CALL, agent_id="agent:child",
        payload=transcript.assistant_tool_call_payload("", [{"id": "c9", "name": "read_file", "arguments": '{"path": "privado.txt"}'}]),
    )
    store.delta(first, "Pronto.")
    store.finish(first)

    second = _follow_up(store, str(first["conversation_id"]))
    assert "privado.txt" not in str(store.history_for_turn(second, rehydration_budget_tokens=10_000))


def test_recording_an_unknown_step_kind_is_ignored_not_stored() -> None:
    store = _store()
    first = _first_turn(store)
    store.record_step(first, kind="something_else", payload={"a": 1})
    assert store.turn_steps(str(first["conversation_id"]), turn_ids=[str(first["turn_id"])]) == {}


def test_a_broken_step_never_raises() -> None:
    """The transcript is an enrichment; it must not be able to end a turn."""
    store = _store()
    store.record_step(
        {"turn_id": "ghost", "conversation_id": "ghost", "user_id": "user-1"},
        kind=transcript.STEP_TOOL_RESULT, payload={"content": object()},
    )


# -- contract continuity ----------------------------------------------------


_CONTRACT_ARGUMENTS = (
    '{"objective": "Reformular o orçamento com margem de 18%.", '
    '"acceptance": [{"id": "total", "check": "o total reflete 18%", "how": "inspection"}], '
    '"toolkits": ["files"]}'
)


def test_a_later_turn_finds_the_contract_the_conversation_is_working_under() -> None:
    """A follow-up resumes the task instead of re-planning it."""
    store = _store()
    first = _first_turn(store)
    store.record_step(
        first, kind=transcript.STEP_ASSISTANT_TOOL_CALL,
        payload=transcript.assistant_tool_call_payload(
            "", [{"id": "c1", "name": "write_contract", "arguments": _CONTRACT_ARGUMENTS}],
        ),
    )

    found = store.latest_contract(str(first["conversation_id"]))

    assert found is not None
    assert found["objective"].startswith("Reformular")


def test_the_most_recent_contract_wins_when_the_agent_revised_it() -> None:
    store = _store()
    first = _first_turn(store)
    store.record_step(
        first, kind=transcript.STEP_ASSISTANT_TOOL_CALL,
        payload=transcript.assistant_tool_call_payload("", [{"id": "c1", "name": "write_contract", "arguments": _CONTRACT_ARGUMENTS}]),
    )
    store.record_step(
        first, kind=transcript.STEP_ASSISTANT_TOOL_CALL,
        payload=transcript.assistant_tool_call_payload(
            "", [{"id": "c2", "name": "write_contract", "arguments": _CONTRACT_ARGUMENTS.replace("18%", "22%")}],
        ),
    )

    assert "22%" in str(store.latest_contract(str(first["conversation_id"])))


def test_a_conversation_that_never_planned_has_no_contract() -> None:
    store = _store()
    first = _first_turn(store)
    _record_a_read(store, first, path="a.txt", content="x")

    assert store.latest_contract(str(first["conversation_id"])) is None
