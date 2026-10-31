from __future__ import annotations

from sqlalchemy import create_engine

from agentos.agentic import transcript
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata


def _store() -> PostgresChatStore:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return PostgresChatStore(engine)


def _create_turn(
    store: PostgresChatStore,
    *,
    message: str,
    provider: str = "anthropic",
    model_id: str = "claude-sonnet-4-6",
    key: str = "req-1",
    conversation_id: str | None = None,
) -> dict[str, object]:
    receipt = store.create(
        user_id="user-1",
        message=message,
        provider=provider,
        model_id=model_id,
        idempotency_key=key,
        conversation_id=conversation_id,
    )
    turn = store.claim(receipt.turn_id)
    assert turn is not None
    return turn


def test_history_for_turn_deduplicates_pre_tool_narration_across_multiple_steps() -> None:
    store = _store()
    first = _create_turn(store, message="Analise o projeto e corrija o bug.", key="req-1")

    # Iteration 1: assistant narrates "Passo 1." and calls read_file
    store.delta(first, "Passo 1.")
    store.record_step(
        first,
        kind=transcript.STEP_ASSISTANT_TOOL_CALL,
        payload=transcript.assistant_tool_call_payload(
            "Passo 1.",
            [{"id": "call-1", "name": "read_file", "arguments": '{"path": "app.py"}'}],
        ),
    )
    store.record_step(
        first,
        kind=transcript.STEP_TOOL_RESULT,
        payload=transcript.tool_result_payload(
            call_id="call-1",
            name="read_file",
            status="succeeded",
            content="def bug(): pass",
        ),
        tool_name="read_file",
        tool_call_id="call-1",
    )

    # Iteration 2: assistant narrates "Passo 2." and calls edit_file
    store.delta(first, "Passo 2.")
    store.record_step(
        first,
        kind=transcript.STEP_ASSISTANT_TOOL_CALL,
        payload=transcript.assistant_tool_call_payload(
            "Passo 2.",
            [{"id": "call-2", "name": "edit_file", "arguments": '{"path": "app.py"}'}],
        ),
    )
    store.record_step(
        first,
        kind=transcript.STEP_TOOL_RESULT,
        payload=transcript.tool_result_payload(
            call_id="call-2",
            name="edit_file",
            status="succeeded",
            content="updated app.py",
        ),
        tool_name="edit_file",
        tool_call_id="call-2",
    )

    # Iteration 3 (final): assistant emits final answer
    store.delta(first, "Resposta final.")
    store.finish(first)

    second = _create_turn(
        store,
        message="Obrigado, pode confirmar?",
        key="req-2",
        conversation_id=str(first["conversation_id"]),
    )
    history = store.history_for_turn(second, rehydration_budget_tokens=10_000)

    # Expected sequence of messages for Turn 1 + Turn 2 user request:
    # 0: user ("Analise o projeto e corrija o bug.")
    # 1: assistant step 1 ("Passo 1." + tool_use call-1)
    # 2: user tool_result call-1
    # 3: assistant step 2 ("Passo 2." + tool_use call-2)
    # 4: user tool_result call-2
    # 5: assistant final answer ("Resposta final." ONLY, without "Passo 1." or "Passo 2.")
    # 6: user ("Obrigado, pode confirmar?")
    assert len(history) == 7
    final_assistant = history[5]
    assert final_assistant["role"] == "assistant"
    assert final_assistant["content"] == "Resposta final."
    assert "Passo 1." not in str(final_assistant["content"])
    assert "Passo 2." not in str(final_assistant["content"])
    assert isinstance(final_assistant.get("_sequence"), int)


def test_history_for_turn_omits_trailing_assistant_message_when_turn_ended_right_after_tool_call() -> None:
    store = _store()
    first = _create_turn(
        store,
        message="Rode o diagnóstico.",
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
        key="req-1",
    )

    # Turn 1 emitted "Passo 1.", ran a tool, and ended with no post-tool text
    store.delta(first, "Passo 1.")
    store.record_step(
        first,
        kind=transcript.STEP_ASSISTANT_TOOL_CALL,
        payload=transcript.assistant_tool_call_payload(
            "Passo 1.",
            [{"id": "call-1", "name": "run_command", "arguments": '{"command": "pytest"}'}],
        ),
    )
    store.record_step(
        first,
        kind=transcript.STEP_TOOL_RESULT,
        payload=transcript.tool_result_payload(
            call_id="call-1",
            name="run_command",
            status="succeeded",
            content="all green",
        ),
        tool_name="run_command",
        tool_call_id="call-1",
    )
    store.finish(first)

    second = _create_turn(
        store,
        message="Continue.",
        provider="openrouter",
        model_id="anthropic/claude-sonnet-4",
        key="req-2",
        conversation_id=str(first["conversation_id"]),
    )
    history = store.history_for_turn(second, rehydration_budget_tokens=10_000)

    # Expected:
    # 0: user ("Rode o diagnóstico.")
    # 1: assistant tool call ("Passo 1." + tool_calls)
    # 2: tool result ("all green") -> carries _sequence from the assistant row
    # 3: user ("Continue.")
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "user"]
    assert history[1]["content"] == "Passo 1."
    assert isinstance(history[2].get("_sequence"), int)


def test_history_for_turn_partial_budget_strips_prefix_without_corrupting_final_answer() -> None:
    store = _store()
    first = _create_turn(store, message="Analise e edite.", key="req-p1")

    # Step 1: large tool result that will be dropped by tight budget
    store.delta(first, "Vou ler o arquivo gigante.")
    store.record_step(
        first,
        kind=transcript.STEP_ASSISTANT_TOOL_CALL,
        payload=transcript.assistant_tool_call_payload(
            "Vou ler o arquivo gigante.",
            [{"id": "call-big", "name": "read_file", "arguments": '{"path": "huge.txt"}'}],
        ),
    )
    store.record_step(
        first,
        kind=transcript.STEP_TOOL_RESULT,
        payload=transcript.tool_result_payload(
            call_id="call-big",
            name="read_file",
            status="succeeded",
            content="X" * 4_000,
        ),
        tool_name="read_file",
        tool_call_id="call-big",
    )

    # Step 2: small tool result that fits within tight budget; note its text "Ok" also appears inside the final answer!
    store.delta(first, "Ok")
    store.record_step(
        first,
        kind=transcript.STEP_ASSISTANT_TOOL_CALL,
        payload=transcript.assistant_tool_call_payload(
            "Ok",
            [{"id": "call-small", "name": "edit_file", "arguments": '{"path": "a.py"}'}],
        ),
    )
    store.record_step(
        first,
        kind=transcript.STEP_TOOL_RESULT,
        payload=transcript.tool_result_payload(
            call_id="call-small",
            name="edit_file",
            status="succeeded",
            content="done",
        ),
        tool_name="edit_file",
        tool_call_id="call-small",
    )

    # Final answer contains "Ok" as a substring
    store.delta(first, "Resultado: Ok, tudo finalizado.")
    store.finish(first)

    second = _create_turn(
        store,
        message="Próximo passo",
        key="req-p2",
        conversation_id=str(first["conversation_id"]),
    )
    # Budget of 200 tokens drops Step 1 (~1000 tokens) and keeps Step 2 (< 60 tokens)
    history = store.history_for_turn(second, rehydration_budget_tokens=200)

    final_assistant = history[-2]
    assert final_assistant["role"] == "assistant"
    assert final_assistant["content"] == "Resultado: Ok, tudo finalizado."
