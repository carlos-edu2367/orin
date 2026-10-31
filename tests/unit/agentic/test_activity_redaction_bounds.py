from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, insert, update

from agentos.agentic.events import AgentActivityEvent, AgentActivityEventType
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import (
    conversation_activity_events,
    conversation_dispatches,
    metadata,
)
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore


def test_redaction_expansion_does_not_exceed_output_tail_limit() -> None:
    # 7 occurrences of '"requiresApiKey": false' expand by +5 chars each (+35 chars total) when redacted
    repeated = '"requiresApiKey": false, ' * 7
    padding = "x" * (2000 - len(repeated))
    raw_tail = repeated + padding
    assert len(raw_tail) == 2000

    event = AgentActivityEvent(
        event_id="evt_1",
        conversation_id="conv_1",
        turn_id="turn_1",
        execution_id="exec_1",
        user_id="user_1",
        workspace_id=None,
        agent_id="agent:conv_1:main",
        parent_agent_id=None,
        event_type=AgentActivityEventType.TOOL_PROGRESSED,
        sequence=1,
        summary="Executando: Find Ashy providers.json",
        payload={"tool_name": "powershell", "output_tail": raw_tail},
        created_at=datetime.now(UTC),
    )
    assert len(str(event.payload["output_tail"])) <= 2000


def test_direct_string_exceeding_text_limit_before_redaction_still_raises() -> None:
    with pytest.raises(ValueError, match="payload text exceeds limit"):
        AgentActivityEvent(
            event_id="evt_over",
            conversation_id="conv_1",
            turn_id="turn_1",
            execution_id="exec_1",
            user_id="user_1",
            workspace_id=None,
            agent_id="agent:conv_1:main",
            parent_agent_id=None,
            event_type=AgentActivityEventType.TOOL_PROGRESSED,
            sequence=1,
            summary="Executando",
            payload={"tool_name": "powershell", "output_tail": "x" * 2001},
            created_at=datetime.now(UTC),
        )


def test_replay_heals_legacy_poisoned_rows_over_2000_chars() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    store = PostgresAgenticActivityStore(engine, cursor_secret="test-secret")

    # Simulate an already-poisoned row in SQLite with 2040-char output_tail
    with engine.begin() as conn:
        conn.execute(
            insert(conversation_activity_events).values(
                event_id="evt_poisoned",
                conversation_id="conv_1",
                user_id="user_1",
                workspace_id=None,
                turn_id="turn_1",
                execution_id="exec_1",
                agent_id="agent:conv_1:main",
                parent_agent_id=None,
                event_type="tool.progressed",
                sequence=445,
                summary="Executando: Find Ashy providers.json",
                payload={"tool_name": "powershell", "output_tail": "a" * 2040},
                visibility="public",
                created_at=datetime.now(UTC),
            )
        )

    page = store.replay("user_1", "conv_1")
    assert len(page.events) == 1
    assert len(str(page.events[0].payload["output_tail"])) == 2000


def test_activity_with_many_artifacts_is_truncated_not_dropped() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    activity_store = PostgresAgenticActivityStore(engine, cursor_secret="test-secret")
    chat_store = PostgresChatStore(engine, activity_store=activity_store)

    turn = {
        "turn_id": "turn_1",
        "conversation_id": "conv_1",
        "execution_id": "exec_1",
        "user_id": "user_1",
    }
    many_artifacts = [{"path": f"file_{i}.txt", "action": "created", "size_bytes": i} for i in range(450)]
    chat_store._activity(
        turn,
        AgentActivityEventType.TOOL_FINISHED,
        "Finalizou powershell",
        {
            "tool_name": "powershell",
            "invocation_id": "call_1",
            "artifacts": many_artifacts,
            "changed_paths": [f"file_{i}.txt" for i in range(40)],
        },
    )
    page = activity_store.replay("user_1", "conv_1")
    assert len(page.events) == 1
    assert page.events[0].event_type == AgentActivityEventType.TOOL_FINISHED


def test_generating_tool_call_includes_invocation_id_and_is_dropped_on_settled_turns() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    activity_store = PostgresAgenticActivityStore(engine, cursor_secret="test-secret")
    chat_store = PostgresChatStore(engine, activity_store=activity_store)

    receipt = chat_store.create(
        user_id="user_1",
        message="Run tool",
        provider="openrouter",
        model_id="model-a",
        idempotency_key="req-gen-tool",
    )
    turn = chat_store.claim(receipt.turn_id)
    assert turn is not None

    ctx = SimpleNamespace(conversation_id=receipt.conversation_id, turn_id=receipt.turn_id)
    chat_store.lifecycle(
        ctx,
        "generating_tool_call",
        tool_name="powershell",
        call_id="call_abc",
        bytes_received=128,
        iteration=1,
    )

    # While turn is still running, snapshot keeps tool.progressed and includes invocation_id + signed cursor
    running_snapshot = chat_store.get(receipt.conversation_id, "user_1")
    progressed = [a for a in running_snapshot["activities"] if a["event_type"] == "tool.progressed"]
    assert len(progressed) == 1
    assert progressed[0]["payload"]["call_id"] == "call_abc"
    assert progressed[0]["payload"]["invocation_id"] == "call_abc"
    assert str(progressed[0]["cursor"]).startswith("a.")

    # Once turn is finished (settled), tool.progressed (including generating_arguments) is dropped
    chat_store.finish(turn)
    settled_snapshot = chat_store.get(receipt.conversation_id, "user_1")
    settled_progressed = [a for a in settled_snapshot["activities"] if a["event_type"] == "tool.progressed"]
    assert settled_progressed == []
    for item in settled_snapshot["activities"]:
        assert str(item["cursor"]).startswith("a.")


def test_recover_stale_respects_fresh_lease_renewed_at() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    chat_store = PostgresChatStore(engine)

    receipt_alive = chat_store.create(
        user_id="user_1",
        message="Long running turn with renewed lease",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="req-alive",
    )
    receipt_dead = chat_store.create(
        user_id="user_1",
        message="Abandoned turn without renewed lease",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        idempotency_key="req-dead",
    )
    assert chat_store.claim(receipt_alive.turn_id) is not None
    assert chat_store.claim(receipt_dead.turn_id) is not None

    old_time = datetime.now(UTC) - timedelta(minutes=30)
    fresh_time = datetime.now(UTC) - timedelta(seconds=10)
    with engine.begin() as conn:
        conn.execute(
            update(conversation_dispatches)
            .where(conversation_dispatches.c.turn_id == receipt_alive.turn_id)
            .values(acquired_at=old_time, lease_renewed_at=fresh_time)
        )
        conn.execute(
            update(conversation_dispatches)
            .where(conversation_dispatches.c.turn_id == receipt_dead.turn_id)
            .values(acquired_at=old_time, lease_renewed_at=old_time)
        )

    recovered = chat_store.recover_stale(maximum_age=timedelta(seconds=120))
    assert recovered == (receipt_dead.turn_id,)


def test_adversarial_deeply_nested_and_wide_payloads_healed_on_replay_and_activity() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    activity_store = PostgresAgenticActivityStore(engine, cursor_secret="test-secret")
    chat_store = PostgresChatStore(engine, activity_store=activity_store)

    # 1. Deeply nested container exceeding MAX_ACTION_DEPTH (4) + wide list of dicts exceeding MAX_ACTION_ITEMS (64)
    # + blank keys + oversized normal strings (> 512 chars) + invalid visibility ("user") + oversized summary
    deep_tree: dict[str, object] = {"leaf": "x" * 900}
    for level in range(8):
        deep_tree = {f"level_{level}": deep_tree}

    poisoned_payload = {
        "   ": "blank_key_should_be_dropped",
        "deep": deep_tree,
        "wide_list": [{"k1": "v" * 700, "k2": i, "k3": [1, 2, 3, 4]} for i in range(50)],
        "output_tail": ('"requiresApiKey": false, ' * 10) + ("z" * 2000),
    }

    with engine.begin() as conn:
        conn.execute(
            insert(conversation_activity_events).values(
                event_id="evt_adv_1",
                conversation_id="conv_adv",
                user_id="user_1",
                workspace_id=None,
                turn_id="turn_adv",
                execution_id="exec_adv",
                agent_id="agent:conv_adv:main",
                parent_agent_id=None,
                event_type="tool.finished",
                sequence=1,
                summary="S" * 800,
                payload=poisoned_payload,
                visibility="user",
                created_at=datetime.now(UTC),
            )
        )

    page = activity_store.replay("user_1", "conv_adv")
    assert len(page.events) == 1
    ev = page.events[0]
    assert len(ev.summary) == 512
    assert len(str(ev.payload["output_tail"])) <= 2000
    assert "   " not in ev.payload

    # 2. Recording via chat_store._activity with adversarial payload also succeeds without dropping
    turn = {
        "turn_id": "turn_adv",
        "conversation_id": "conv_adv",
        "execution_id": "exec_adv",
        "user_id": "user_1",
    }
    chat_store._activity(
        turn,
        AgentActivityEventType.TOOL_FINISHED,
        "A" * 900,
        poisoned_payload,
    )
    page_after = activity_store.replay("user_1", "conv_adv")
    assert len(page_after.events) == 2


def test_snapshot_activity_cursors_are_valid_for_activity_store_replay() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    activity_store = PostgresAgenticActivityStore(engine, cursor_secret="test-secret")
    chat_store = PostgresChatStore(engine, activity_store=activity_store)

    receipt = chat_store.create(
        user_id="user_1",
        message="Test cursors",
        provider="openrouter",
        model_id="model-a",
        idempotency_key="req-cursors",
    )
    turn = chat_store.claim(receipt.turn_id)
    assert turn is not None
    chat_store.delta(turn, "Parte 1 ")
    chat_store.delta(turn, "Parte 2")
    chat_store.record(
        turn,
        AgentActivityEventType.TOOL_FINISHED,
        "Finalizou powershell",
        {"tool_name": "powershell", "invocation_id": "call_1", "status": "succeeded"},
    )
    chat_store.finish(turn)

    snapshot = chat_store.get(receipt.conversation_id, "user_1")
    activities = snapshot["activities"]
    assert len(activities) >= 2
    first_cursor = str(activities[0]["cursor"])
    assert first_cursor.startswith("a.")

    # Passing any activity's cursor to replay() must decode cleanly and return only newer events
    replayed = activity_store.replay("user_1", receipt.conversation_id, cursor=first_cursor)
    assert len(replayed.events) >= 1

