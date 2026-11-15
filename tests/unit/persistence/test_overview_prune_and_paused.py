from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import insert, select, update

from agentos.agentic.events import AgentActivityEventType
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.execution_adapters import (
    ExecutionApplicationAdapter,
    ExecutionQueryAdapter,
)
from agentos.persistence.postgres.migrate import downgrade, upgrade
from agentos.persistence.postgres.schema import (
    code_mode_runs,
    conversation_activity_events,
    conversation_dispatches,
    conversation_events,
    conversation_messages,
    conversation_turns,
    conversations,
    metadata,
)
from agentos.retrieval.embeddings.ollama import (
    EmbeddingUnavailable,
    OllamaEmbedder,
)
from agentos.workers import scheduler as scheduler_mod
from agentos.workers.chat import ChatWorker


@pytest.fixture()
def engine():
    eng = sa.create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(eng)
    return eng


def test_migration_0061_activity_events_overview_index_idempotent(tmp_path):
    db_file = tmp_path / "mig_0061.sqlite"
    eng = sa.create_engine(f"sqlite:///{db_file}")
    try:
        upgrade(eng, "0060_discord_bridge_and_session_watches")
        upgrade(eng, "head")
        # Idempotent re-run
        upgrade(eng, "head")

        indexes = {
            idx["name"]: idx["column_names"]
            for idx in sa.inspect(eng).get_indexes("conversation_activity_events")
        }
        assert "ix_conversation_activity_conv_type_id" in indexes
        assert indexes["ix_conversation_activity_conv_type_id"] == [
            "conversation_id",
            "event_type",
            "id",
        ]

        downgrade(eng, "0060_discord_bridge_and_session_watches")
        indexes_after = {
            idx["name"]
            for idx in sa.inspect(eng).get_indexes("conversation_activity_events")
        }
        assert "ix_conversation_activity_conv_type_id" not in indexes_after

        # Re-upgrade cleanly
        upgrade(eng, "head")
        indexes_reup = {
            idx["name"]
            for idx in sa.inspect(eng).get_indexes("conversation_activity_events")
        }
        assert "ix_conversation_activity_conv_type_id" in indexes_reup
    finally:
        eng.dispose()


def test_overview_counts_all_events_via_sql_and_caps_messages_and_errors(engine):
    activity_store = PostgresAgenticActivityStore(engine, cursor_secret="test-secret")
    store = PostgresChatStore(engine, activity_store=activity_store)
    receipt = store.create(
        user_id="u1",
        message="run overview",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="m-overview-1",
    )
    cid = receipt.conversation_id
    tid = receipt.turn_id

    other_receipt = store.create(
        user_id="u2",
        message="other user",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="m-overview-other",
    )
    other_cid = other_receipt.conversation_id

    now = datetime.now(timezone.utc)
    # Note: store.create already recorded 1 TURN_STARTED event on cid (sequence=1)
    rows = []
    seq = 10
    for i in range(300):
        rows.append(
            {
                "event_id": f"evt-delta-{i}",
                "conversation_id": cid,
                "user_id": "u1",
                "workspace_id": None,
                "turn_id": tid,
                "execution_id": f"exec-{tid}",
                "agent_id": "main",
                "parent_agent_id": None,
                "event_type": "assistant.delta",
                "sequence": seq,
                "summary": f"delta {i}",
                "payload": {"text": "x" * 200},
                "visibility": "public",
                "created_at": now,
            }
        )
        seq += 1

    rows.append(
        {
            "event_id": "evt-tool-fin-1",
            "conversation_id": cid,
            "user_id": "u1",
            "workspace_id": None,
            "turn_id": tid,
            "execution_id": f"exec-{tid}",
            "agent_id": "main",
            "parent_agent_id": None,
            "event_type": "tool.finished",
            "sequence": seq,
            "summary": "Edited file",
            "payload": {"tool_name": "edit_file", "tool_kind": "filesystem", "status": "succeeded"},
            "visibility": "public",
            "created_at": now,
        }
    )
    seq += 1

    for i in range(250):
        rows.append(
            {
                "event_id": f"evt-msg-{i}",
                "conversation_id": cid,
                "user_id": "u1",
                "workspace_id": None,
                "turn_id": tid,
                "execution_id": f"exec-{tid}",
                "agent_id": f"sub-{i % 3}",
                "parent_agent_id": "main",
                "event_type": "agent.message_sent",
                "sequence": seq,
                "summary": f"msg {i}",
                "payload": {"to_agent_id": "main", "content": f"hello {i}"},
                "visibility": "public",
                "created_at": now,
            }
        )
        seq += 1

    for i in range(250):
        rows.append(
            {
                "event_id": f"evt-err-{i}",
                "conversation_id": cid,
                "user_id": "u1",
                "workspace_id": None,
                "turn_id": tid,
                "execution_id": f"exec-{tid}",
                "agent_id": "main",
                "parent_agent_id": None,
                "event_type": "turn.failed",
                "sequence": seq,
                "summary": f"err {i}",
                "payload": {"error_code": f"ERR_{i}"},
                "visibility": "public",
                "created_at": now,
            }
        )
        seq += 1

    # Noise for another user/conversation that must not leak into u1's overview count
    rows.append(
        {
            "event_id": "evt-other-user",
            "conversation_id": other_cid,
            "user_id": "u2",
            "workspace_id": None,
            "turn_id": other_receipt.turn_id,
            "execution_id": "exec-other",
            "agent_id": "main",
            "parent_agent_id": None,
            "event_type": "turn.failed",
            "sequence": 99,
            "summary": "other user err",
            "payload": {"error_code": "ERR_OTHER"},
            "visibility": "public",
            "created_at": now,
        }
    )

    with engine.begin() as c:
        c.execute(insert(conversation_activity_events), rows)

    ov = store.overview(cid, "u1")
    # 1 initial turn.started + 300 deltas + 1 tool.finished + 250 messages + 250 errors = 802
    assert ov["activity_count"] == 1 + 300 + 1 + 250 + 250
    assert ov["tools"] == [
        {
            "tool_name": "edit_file",
            "kind": "filesystem",
            "count": 1,
            "failures": 0,
            "agent_id": "main",
        }
    ]
    assert len(ov["messages"]) == 200
    assert ov["messages"][0]["preview"] == "hello 50"
    assert ov["messages"][-1]["preview"] == "hello 249"
    assert len(ov["errors"]) == 200
    assert ov["errors"][0]["summary"] == "err 50"
    assert ov["errors"][-1]["summary"] == "err 249"


def test_prune_transient_activity_events_preserves_tool_started_and_snapshot_enrichment(
    engine,
):
    activity_store = PostgresAgenticActivityStore(engine, cursor_secret="test-secret")
    store = PostgresChatStore(engine, activity_store=activity_store)

    # Turn 1: old completed turn (10 days ago)
    r1 = store.create(
        user_id="u1",
        message="first turn",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="m-prune-1",
    )
    cid = r1.conversation_id
    t1 = r1.turn_id
    old_time = datetime.now(timezone.utc) - timedelta(days=10)
    with engine.begin() as c:
        c.execute(
            update(conversation_turns)
            .where(conversation_turns.c.turn_id == t1)
            .values(
                state="completed",
                finished_at=old_time,
                updated_at=old_time,
            )
        )
        c.execute(
            update(conversation_dispatches)
            .where(conversation_dispatches.c.turn_id == t1)
            .values(state="completed", updated_at=old_time)
        )
        c.execute(
            update(conversations)
            .where(conversations.c.conversation_id == cid)
            .values(state="completed", updated_at=old_time)
        )

    # Turn 2: recent completed turn (1 day ago)
    r2 = store.create(
        user_id="u1",
        conversation_id=cid,
        message="second turn",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="m-prune-2",
    )
    t2 = r2.turn_id
    recent_time = datetime.now(timezone.utc) - timedelta(days=1)
    with engine.begin() as c:
        c.execute(
            update(conversation_turns)
            .where(conversation_turns.c.turn_id == t2)
            .values(
                state="completed",
                finished_at=recent_time,
                updated_at=recent_time,
            )
        )
        c.execute(
            update(conversation_dispatches)
            .where(conversation_dispatches.c.turn_id == t2)
            .values(state="completed", updated_at=recent_time)
        )
        c.execute(
            update(conversations)
            .where(conversations.c.conversation_id == cid)
            .values(state="completed", updated_at=recent_time)
        )

    # Turn 3: old paused turn (non-terminal, must NOT be pruned)
    r3 = store.create(
        user_id="u1",
        conversation_id=cid,
        message="third turn",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="m-prune-3",
    )
    t3 = r3.turn_id
    with engine.begin() as c:
        c.execute(
            update(conversation_turns)
            .where(conversation_turns.c.turn_id == t3)
            .values(
                state="paused",
                finished_at=None,
                updated_at=old_time,
            )
        )

    def _make_event(eid: str, turn_id: str, seq: int, etype: str, payload: dict):
        return {
            "event_id": eid,
            "conversation_id": cid,
            "user_id": "u1",
            "workspace_id": None,
            "turn_id": turn_id,
            "execution_id": f"exec-{turn_id}",
            "agent_id": "main",
            "parent_agent_id": None,
            "event_type": etype,
            "sequence": seq,
            "summary": etype,
            "payload": payload,
            "visibility": "public",
            "created_at": old_time,
        }

    with engine.begin() as c:
        c.execute(
            insert(conversation_activity_events),
            [
                _make_event("t1-delta-1", t1, 10, "assistant.delta", {"text": "a"}),
                _make_event("t1-thought-1", t1, 11, "agent.thought_delta", {"text": "t"}),
                _make_event("t1-req-1", t1, 12, "tool.requested", {"tool_name": "bash"}),
                _make_event(
                    "t1-start-1",
                    t1,
                    13,
                    "tool.started",
                    {
                        "tool_name": "bash",
                        "invocation_id": "inv-42",
                        "command": "pytest -q",
                        "path": "src/main.py",
                    },
                ),
                _make_event(
                    "t1-prog-1",
                    t1,
                    14,
                    "tool.progressed",
                    {"invocation_id": "inv-42", "progress": 50},
                ),
                _make_event(
                    "t1-fin-1",
                    t1,
                    15,
                    "tool.finished",
                    {
                        "tool_name": "bash",
                        "invocation_id": "inv-42",
                        "exit_code": 0,
                    },
                ),
                _make_event("t1-speak-1", t1, 16, "speak.delta", {"text": "hi"}),
                _make_event("t2-delta-1", t2, 10, "assistant.delta", {"text": "recent"}),
                _make_event("t3-delta-1", t3, 10, "assistant.delta", {"text": "paused"}),
            ],
        )

    deleted = store.prune_transient_activity_events(
        older_than_days=7, batch_size=2, max_batches=10
    )
    assert deleted == 5

    with engine.begin() as c:
        remaining_t1 = [
            r["event_type"]
            for r in c.execute(
                select(conversation_activity_events.c.event_type)
                .where(conversation_activity_events.c.turn_id == t1)
                .order_by(conversation_activity_events.c.sequence)
            ).mappings()
        ]
        remaining_t2 = [
            r["event_type"]
            for r in c.execute(
                select(conversation_activity_events.c.event_type)
                .where(conversation_activity_events.c.turn_id == t2)
                .order_by(conversation_activity_events.c.sequence)
            ).mappings()
        ]
        remaining_t3 = [
            r["event_type"]
            for r in c.execute(
                select(conversation_activity_events.c.event_type)
                .where(conversation_activity_events.c.turn_id == t3)
                .order_by(conversation_activity_events.c.sequence)
            ).mappings()
        ]

    # turn.started, tool.started, and tool.finished MUST remain on t1
    assert remaining_t1 == ["turn.started", "tool.started", "tool.finished"]
    # Recent completed turn and paused turn must still have their assistant.delta
    assert "assistant.delta" in remaining_t2
    assert "assistant.delta" in remaining_t3

    # Verify _compact_snapshot_activities still enriches tool.finished from tool.started!
    snapshot = store.get(cid, "u1")
    t1_tools = [
        a
        for a in snapshot["activities"]
        if a["turn_id"] == t1 and a["event_type"] == "tool.finished"
    ]
    assert len(t1_tools) == 1
    assert t1_tools[0]["payload"]["command"] == "pytest -q"
    assert t1_tools[0]["payload"]["path"] == "src/main.py"


def test_store_activity_bumps_conversation_updated_at_on_interactive_events(engine):
    activity_store = PostgresAgenticActivityStore(engine, cursor_secret="test-secret")
    store = PostgresChatStore(engine, activity_store=activity_store)
    receipt = store.create(
        user_id="u1",
        message="check permission",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="m-bump-1",
    )
    cid = receipt.conversation_id
    turn = store.claim(receipt.turn_id)
    assert turn is not None

    old_ts = datetime(2020, 1, 1, tzinfo=timezone.utc)

    def _reset_conv_updated_at():
        with engine.begin() as c:
            c.execute(
                update(conversations)
                .where(conversations.c.conversation_id == cid)
                .values(updated_at=old_ts)
            )

    def _get_conv_updated_at() -> datetime:
        with engine.begin() as c:
            val = c.execute(
                select(conversations.c.updated_at).where(
                    conversations.c.conversation_id == cid
                )
            ).scalar_one()
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)

    # Non-interactive delta must NOT bump conversations.c.updated_at
    _reset_conv_updated_at()
    store._activity(
        turn,
        AgentActivityEventType.ASSISTANT_DELTA,
        "delta",
        {"content": "hello"},
    )
    assert _get_conv_updated_at() == old_ts

    # Interactive events MUST bump conversations.c.updated_at
    interactive_types = [
        AgentActivityEventType.TOOL_PERMISSION_REQUIRED,
        AgentActivityEventType.CODE_MODE_PLAN_READY,
        AgentActivityEventType.CODE_MODE_DECISION_REQUIRED,
        AgentActivityEventType.WORKSPACE_TRUST_REQUIRED,
        AgentActivityEventType.TURN_WAITING_USER,
    ]
    for etype in interactive_types:
        _reset_conv_updated_at()
        store._activity(turn, etype, f"interactive {etype}", {})
        assert _get_conv_updated_at() > old_ts, f"Expected bump for {etype}"


def test_expire_abandoned_paused_turns_unblocks_conversation_and_preserves_partial_content(
    engine,
):
    activity_store = PostgresAgenticActivityStore(engine, cursor_secret="test-secret")
    store = PostgresChatStore(engine, activity_store=activity_store)

    receipt = store.create(
        user_id="u1",
        message="iniciar tarefa longa",
        provider="codex_cli",
        model_id="gpt-5",
        idempotency_key="m-paused-1",
        code_mode="code",
        cli_options='{"resume": "sess-123"}',
    )
    cid = receipt.conversation_id
    tid = receipt.turn_id
    aid = receipt.message_id

    turn = store.claim(tid)
    assert turn is not None

    store.delta(turn, "Conteúdo parcial preservado com sucesso.")
    store.pause_for_reconciliation(turn, code="PROCESS_RECONCILE_LOST")

    # Transition canonical Execution to PAUSED (matching worker runtime)
    exec_queries = ExecutionQueryAdapter(engine)
    exec_app = ExecutionApplicationAdapter(engine)
    now = datetime.now(timezone.utc)
    for idx, target in enumerate(("STARTING", "RUNNING", "PAUSED"), start=1):
        view = exec_queries.get(
            {
                "resource_id": str(turn["execution_id"]),
                "user_id": "u1",
                "purpose": "execution.read",
            }
        )
        exec_app.transition(
            {
                "execution_id": str(turn["execution_id"]),
                "user_id": "u1",
                "target_state": target,
                "reason_code": "test_setup",
                "result_ref": None,
                "expected_state_version": int(view["state_version"]),
                "idempotency_key": f"setup-paused-{idx}",
                "requested_at": now,
            }
        )

    # Backdate paused turn by 3 hours
    stale_ts = datetime.now(timezone.utc) - timedelta(hours=3)
    with engine.begin() as c:
        c.execute(
            update(conversation_turns)
            .where(conversation_turns.c.turn_id == tid)
            .values(updated_at=stale_ts)
        )
        c.execute(
            update(conversation_dispatches)
            .where(conversation_dispatches.c.turn_id == tid)
            .values(updated_at=stale_ts)
        )

    expired = store.expire_abandoned_paused_turns(older_than=timedelta(hours=2))
    assert tid in expired

    with engine.begin() as c:
        t_row = (
            c.execute(
                select(conversation_turns).where(
                    conversation_turns.c.turn_id == tid
                )
            )
            .mappings()
            .one()
        )
        d_row = (
            c.execute(
                select(conversation_dispatches).where(
                    conversation_dispatches.c.turn_id == tid
                )
            )
            .mappings()
            .one()
        )
        m_row = (
            c.execute(
                select(conversation_messages).where(
                    conversation_messages.c.message_id == aid
                )
            )
            .mappings()
            .one()
        )
        cm_row = (
            c.execute(
                select(code_mode_runs).where(code_mode_runs.c.turn_id == tid)
            )
            .mappings()
            .one()
        )
        conv_row = (
            c.execute(
                select(conversations).where(
                    conversations.c.conversation_id == cid
                )
            )
            .mappings()
            .one()
        )
        ev_rows = (
            c.execute(
                select(conversation_events).where(
                    (conversation_events.c.message_id == aid)
                    & (conversation_events.c.event_type == "turn.failed")
                )
            )
            .mappings()
            .all()
        )

    assert t_row["state"] == "failed"
    assert t_row["finished_at"] is not None
    assert t_row["cli_options"] is None
    assert d_row["state"] == "failed"
    assert m_row["status"] == "failed"
    assert m_row["retryable"] is True
    assert m_row["content"] == "Conteúdo parcial preservado com sucesso."
    assert cm_row["stage"] == "blocked"
    assert conv_row["state"] == "failed"
    assert len(ev_rows) == 1

    exec_after = exec_queries.get(
        {
            "resource_id": str(turn["execution_id"]),
            "user_id": "u1",
            "purpose": "execution.read",
        }
    )
    assert exec_after["state"] == "FAILED"

    follow_up = ChatApplication(store).send("u1", cid, "continuar", "m-paused-2")
    assert follow_up.state == "queued"


def test_expire_abandoned_paused_turns_adversarial_edge_cases(engine):
    store = PostgresChatStore(engine)

    # Case 1: Freshly paused turn (< 2h) is NOT expired
    r1 = store.create(
        user_id="u1",
        message="msg 1",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="m-adv-1",
    )
    c1 = r1.conversation_id
    t1 = r1.turn_id
    turn1 = store.claim(t1)
    assert turn1 is not None
    store.pause_for_reconciliation(turn1, code="FRESH_PAUSE")

    # Case 2: Paused turn whose Execution is already FAILED & conversation has a newer active turn
    r2 = store.create(
        user_id="u1",
        message="msg old",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="m-adv-2",
    )
    c2 = r2.conversation_id
    t2_old = r2.turn_id
    turn2 = store.claim(t2_old)
    assert turn2 is not None
    store.pause_for_reconciliation(turn2, code="OLD_PAUSE")

    # Transition Execution of t2_old directly to FAILED beforehand
    exec_queries = ExecutionQueryAdapter(engine)
    exec_app = ExecutionApplicationAdapter(engine)
    now = datetime.now(timezone.utc)
    for idx, target in enumerate(("STARTING", "FAILED"), start=1):
        v2 = exec_queries.get(
            {
                "resource_id": str(turn2["execution_id"]),
                "user_id": "u1",
                "purpose": "execution.read",
            }
        )
        exec_app.transition(
            {
                "execution_id": str(turn2["execution_id"]),
                "user_id": "u1",
                "target_state": target,
                "reason_code": "already_failed",
                "result_ref": None,
                "expected_state_version": int(v2["state_version"]),
                "idempotency_key": f"adv-fail-{idx}",
                "requested_at": now,
            }
        )

    stale_ts = datetime.now(timezone.utc) - timedelta(hours=4)
    with engine.begin() as c:
        c.execute(
            update(conversation_turns)
            .where(conversation_turns.c.turn_id == t2_old)
            .values(updated_at=stale_ts)
        )
        c.execute(
            update(conversation_dispatches)
            .where(conversation_dispatches.c.turn_id == t2_old)
            .values(updated_at=stale_ts)
        )
        # Insert a newer running turn on c2 and set c2 state="running"
        c.execute(
            insert(conversation_turns).values(
                turn_id="turn_newer_running",
                conversation_id=c2,
                user_id="u1",
                execution_id="exec_newer_running",
                user_message_id="msg_u_new",
                assistant_message_id="msg_a_new",
                state="running",
                provider="openrouter",
                model_id="model-1",
                idempotency_key="m-adv-newer",
                created_at=now,
                updated_at=now,
            )
        )
        c.execute(
            update(conversations)
            .where(conversations.c.conversation_id == c2)
            .values(state="running", updated_at=now)
        )

    worker = ChatWorker(store=store)
    recovered = worker.recover_stale(maximum_age=timedelta(seconds=120))

    assert t1 not in recovered
    assert t2_old in recovered

    with engine.begin() as c:
        c1_state = c.execute(
            select(conversations.c.state).where(
                conversations.c.conversation_id == c1
            )
        ).scalar_one()
        c2_state = c.execute(
            select(conversations.c.state).where(
                conversations.c.conversation_id == c2
            )
        ).scalar_one()
        t2_state = c.execute(
            select(conversation_turns.c.state).where(
                conversation_turns.c.turn_id == t2_old
            )
        ).scalar_one()

    assert c1_state == "paused"
    assert t2_state == "failed"
    # Conversation c2 had a newer running turn so its state must remain "running"
    assert c2_state == "running"


def test_scheduler_adaptive_pruning_interval():
    chat_store = MagicMock()
    # First call saturates batch budget (2000 * 10 = 20_000) -> 60s backoff
    chat_store.prune_transient_activity_events.return_value = 20_000

    t0 = 1000.0
    next_prune = scheduler_mod._maybe_prune_activity_events(
        chat_store, now_mono=t0, next_prune_mono=0.0
    )
    assert next_prune == pytest.approx(t0 + 60.0)

    # Before 60s has elapsed, does not prune again
    chat_store.prune_transient_activity_events.reset_mock()
    next_prune_same = scheduler_mod._maybe_prune_activity_events(
        chat_store, now_mono=t0 + 30.0, next_prune_mono=next_prune
    )
    assert next_prune_same == pytest.approx(next_prune)
    chat_store.prune_transient_activity_events.assert_not_called()

    # After 60s, runs again; partial delete (< 20_000) -> 6h backoff
    chat_store.prune_transient_activity_events.return_value = 42
    t1 = t0 + 61.0
    next_prune_after = scheduler_mod._maybe_prune_activity_events(
        chat_store, now_mono=t1, next_prune_mono=next_prune
    )
    chat_store.prune_transient_activity_events.assert_called_once_with(
        batch_size=2000, max_batches=10
    )
    assert next_prune_after == pytest.approx(t1 + 6 * 3600.0)


def test_ollama_embedder_cooldown_404_5xx_network_empty_and_recovery():
    default_embedder = OllamaEmbedder(
        base_url="http://localhost:11434",
        model="nomic-embed-text",
    )
    assert default_embedder._client.timeout.connect == 3.0

    clock_now = [100.0]
    calls: list[dict] = []
    mode = ["404"]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "body": json.loads(request.content)})
        current = mode[0]
        if current == "404":
            return httpx.Response(404, json={"error": "model 'nomic-embed-text' not found"})
        if current == "503":
            return httpx.Response(503, json={"error": "server overloaded"})
        if current == "400":
            return httpx.Response(400, json={"error": "bad input"})
        if current == "connect_error":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(
            200,
            json={"embeddings": [[0.25, 0.75] for _ in calls[-1]["body"]["input"]]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    embedder = OllamaEmbedder(
        base_url="http://localhost:11434",
        model="nomic-embed-text",
        client=client,
        clock=lambda: clock_now[0],
    )

    # 1. Empty input returns [] without HTTP call
    assert embedder.embed([]) == []
    assert len(calls) == 0

    # 2. HTTP 404 triggers 300s cooldown
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["hello"])
    assert len(calls) == 1

    # Empty input during cooldown STILL returns [] without raising
    assert embedder.embed([]) == []
    assert len(calls) == 1

    # Before 300s expires (e.g. +299s), fails fast without HTTP call
    clock_now[0] = 399.0
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["hello again"])
    assert len(calls) == 1

    # After 300s expires (+301s), retries and recovers on 200 OK
    clock_now[0] = 401.0
    mode[0] = "200"
    vecs = embedder.embed(["recovered"])
    assert vecs == [[0.25, 0.75]]
    assert len(calls) == 2

    # 3. HTTP 503 triggers 60s cooldown
    mode[0] = "503"
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["fail 503"])
    assert len(calls) == 3

    clock_now[0] = 460.0  # +59s -> still in cooldown
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["still cooling down"])
    assert len(calls) == 3

    clock_now[0] = 462.0  # +61s -> cooldown expired
    mode[0] = "200"
    assert embedder.embed(["back up"]) == [[0.25, 0.75]]
    assert len(calls) == 4

    # 4. ConnectError triggers 60s cooldown
    mode[0] = "connect_error"
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["net down"])
    assert len(calls) == 5

    clock_now[0] = 520.0  # +58s -> still in cooldown
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["net still down"])
    assert len(calls) == 5

    clock_now[0] = 523.0  # +61s -> recovers
    mode[0] = "200"
    assert embedder.embed(["net back"]) == [[0.25, 0.75]]
    assert len(calls) == 6

    # 5. Client 400 error raises EmbeddingUnavailable but does NOT enter 60s/300s daemon cooldown
    mode[0] = "400"
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["bad request"])
    assert len(calls) == 7

    mode[0] = "200"
    assert embedder.embed(["immediate retry at same timestamp"]) == [[0.25, 0.75]]
    assert len(calls) == 8


def test_adversarial_overview_malformed_payload_and_prune_batch_cap(engine):
    activity_store = PostgresAgenticActivityStore(engine, cursor_secret="test-secret")
    store = PostgresChatStore(engine, activity_store=activity_store)
    r1 = store.create(
        user_id="u1",
        message="adv overview and prune",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="m-adv-ov-1",
    )
    cid = r1.conversation_id
    t1 = r1.turn_id
    old_time = datetime.now(timezone.utc) - timedelta(days=15)

    with engine.begin() as c:
        c.execute(
            update(conversation_turns)
            .where(conversation_turns.c.turn_id == t1)
            .values(state="failed", finished_at=old_time, updated_at=old_time)
        )
        # Insert malformed non-dict payload on tool.finished + 7 transient rows
        rows = [
            {
                "event_id": "adv-malformed-tool",
                "conversation_id": cid,
                "user_id": "u1",
                "workspace_id": None,
                "turn_id": t1,
                "execution_id": f"exec-{t1}",
                "agent_id": "main",
                "parent_agent_id": None,
                "event_type": "tool.finished",
                "sequence": 50,
                "summary": "malformed payload",
                "payload": ["not", "a", "dict"],
                "visibility": "public",
                "created_at": old_time,
            },
            {
                "event_id": "adv-msg-recv",
                "conversation_id": cid,
                "user_id": "u1",
                "workspace_id": None,
                "turn_id": t1,
                "execution_id": f"exec-{t1}",
                "agent_id": "sub-1",
                "parent_agent_id": "main",
                "event_type": "agent.message_received",
                "sequence": 51,
                "summary": "received from sub",
                "payload": {"from_agent_id": "sub-1", "content": "result ready"},
                "visibility": "public",
                "created_at": old_time,
            },
        ]
        for idx in range(7):
            rows.append(
                {
                    "event_id": f"adv-transient-{idx}",
                    "conversation_id": cid,
                    "user_id": "u1",
                    "workspace_id": None,
                    "turn_id": t1,
                    "execution_id": f"exec-{t1}",
                    "agent_id": "main",
                    "parent_agent_id": None,
                    "event_type": "code_mode.stage_changed",
                    "sequence": 100 + idx,
                    "summary": "stage changed",
                    "payload": {},
                    "visibility": "public",
                    "created_at": old_time,
                }
            )
        c.execute(insert(conversation_activity_events), rows)

    ov = store.overview(cid, "u1")
    assert ov["activity_count"] == 1 + 2 + 7
    assert len(ov["messages"]) == 1
    assert ov["messages"][0]["preview"] == "result ready"

    # Batch cap: batch_size=2, max_batches=2 -> deletes at most 4 rows on first run
    deleted_first = store.prune_transient_activity_events(
        older_than_days=0, batch_size=2, max_batches=2
    )
    assert deleted_first == 4

    deleted_second = store.prune_transient_activity_events(
        older_than_days=7, batch_size=2, max_batches=5
    )
    assert deleted_second == 3


def test_adversarial_permission_resolved_bump_and_missing_execution_paused_expiry(
    engine,
):
    store = PostgresChatStore(engine, activity_store=None)
    r1 = store.create(
        user_id="u1",
        message="perm resolved and missing exec",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="m-adv-perm-1",
    )
    cid = r1.conversation_id
    turn = store.claim(r1.turn_id)
    assert turn is not None

    old_ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with engine.begin() as c:
        c.execute(
            update(conversations)
            .where(conversations.c.conversation_id == cid)
            .values(updated_at=old_ts)
        )

    # Even with activity_store=None and string event_type "tool.permission_resolved", updated_at bumps
    store._activity(turn, "tool.permission_resolved", "Permission resolved", {})
    with engine.begin() as c:
        bumped = c.execute(
            select(conversations.c.updated_at).where(
                conversations.c.conversation_id == cid
            )
        ).scalar_one()
    bumped_aware = bumped if bumped.tzinfo else bumped.replace(tzinfo=timezone.utc)
    assert bumped_aware > old_ts

    # Pause turn and corrupt execution_id so ExecutionQueryAdapter raises LookupError/NotFound
    store.pause_for_reconciliation(turn, code="PROCESS_RECONCILE_LOST")
    stale_ts = datetime.now(timezone.utc) - timedelta(hours=5)
    with engine.begin() as c:
        c.execute(
            update(conversation_turns)
            .where(conversation_turns.c.turn_id == r1.turn_id)
            .values(execution_id="exe_nonexistent_404", updated_at=stale_ts)
        )
        c.execute(
            update(conversation_dispatches)
            .where(conversation_dispatches.c.turn_id == r1.turn_id)
            .values(updated_at=stale_ts)
        )

    expired = store.expire_abandoned_paused_turns(older_than=timedelta(hours=2))
    assert r1.turn_id in expired
    assert store.get(cid, "u1")["state"] == "failed"
