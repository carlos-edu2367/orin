"""Tests for AgentRunStore and AgentWorktreeStore (WS-B0, Spec 6.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from agentos.persistence.postgres.agent_runs import AgentRunRecord, AgentRunStore
from agentos.persistence.postgres.agent_worktrees import AgentWorktreeRecord, AgentWorktreeStore
from agentos.persistence.postgres.migrate import upgrade


def _engine():
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine)
    return engine


def test_agent_run_store_upsert_get_touch_and_list_for_conversation() -> None:
    engine = _engine()
    t0 = datetime(2026, 11, 21, 14, 0, 0, tzinfo=UTC)
    store = AgentRunStore(engine, clock=lambda: t0)

    created = store.upsert(
        agent_id="ag-1",
        user_id="user-1",
        conversation_id="conv-a",
        turn_id="turn-1",
        parent_agent_id="root-1",
        subagent_type="general-purpose",
        provider="anthropic-cli",
        model_id="claude-sonnet-5-5",
        effort="high",
        state="running",
        task_digest="sha256-abc",
        history_path=".orin/histories/ag-1.jsonl",
        worktree_path=".orin/wt/ag-1",
        pipeline_run_id="pipe-42",
        last_activity_at=t0,
        tool_calls=2,
        input_tokens=1500,
        output_tokens=400,
        cost_usd=Decimal("0.012500"),
        result_ref=None,
        created_at=t0,
        finished_at=None,
    )
    assert isinstance(created, AgentRunRecord)
    assert created.agent_id == "ag-1"
    assert created.user_id == "user-1"
    assert created.conversation_id == "conv-a"
    assert created.turn_id == "turn-1"
    assert created.parent_agent_id == "root-1"
    assert created.subagent_type == "general-purpose"
    assert created.provider == "anthropic-cli"
    assert created.model_id == "claude-sonnet-5-5"
    assert created.effort == "high"
    assert created.state == "running"
    assert created.task_digest == "sha256-abc"
    assert created.history_path == ".orin/histories/ag-1.jsonl"
    assert created.worktree_path == ".orin/wt/ag-1"
    assert created.pipeline_run_id == "pipe-42"
    assert created.last_activity_at == t0
    assert created.tool_calls == 2
    assert created.input_tokens == 1500
    assert created.output_tokens == 400
    assert created.cost_usd == pytest.approx(0.0125)
    assert created.result_ref is None
    assert created.created_at == t0
    assert created.finished_at is None

    fetched = store.get("ag-1")
    assert fetched == created
    assert store.get("missing-agent") is None

    # touch updates progress counters, cost and last_activity_at without wiping untouched fields.
    t1 = t0 + timedelta(seconds=45)
    touched = store.touch(
        "ag-1",
        tool_calls=7,
        input_tokens=3200,
        output_tokens=910,
        cost_usd=0.0345,
        last_activity_at=t1,
    )
    assert touched is not None
    assert touched.tool_calls == 7
    assert touched.input_tokens == 3200
    assert touched.output_tokens == 910
    assert touched.cost_usd == pytest.approx(0.0345)
    assert touched.last_activity_at == t1
    assert touched.pipeline_run_id == "pipe-42"
    assert touched.state == "running"
    assert store.touch("missing-agent", tool_calls=1) is None

    # upsert on existing agent_id updates state and result_ref.
    t2 = t0 + timedelta(minutes=2)
    finished = store.upsert(
        agent_id="ag-1",
        user_id="user-1",
        conversation_id="conv-a",
        turn_id="turn-1",
        parent_agent_id="root-1",
        subagent_type="general-purpose",
        provider="anthropic-cli",
        model_id="claude-sonnet-5-5",
        effort="high",
        state="completed",
        task_digest="sha256-abc",
        history_path=".orin/histories/ag-1.jsonl",
        worktree_path=".orin/wt/ag-1",
        pipeline_run_id="pipe-42",
        last_activity_at=t2,
        tool_calls=8,
        input_tokens=3500,
        output_tokens=1100,
        cost_usd=0.04,
        result_ref=".orin/agent-outputs/ag-1.md",
        created_at=t0,
        finished_at=t2,
    )
    assert finished.state == "completed"
    assert finished.result_ref == ".orin/agent-outputs/ag-1.md"
    assert finished.finished_at == t2

    # Separate conversation / user isolation in list_for_conversation.
    store.upsert(
        agent_id="ag-2",
        user_id="user-1",
        conversation_id="conv-a",
        turn_id="turn-2",
        parent_agent_id=None,
        subagent_type="Explore",
        state="queued",
        created_at=t1,
        last_activity_at=t1,
    )
    store.upsert(
        agent_id="ag-other-conv",
        user_id="user-1",
        conversation_id="conv-b",
        turn_id="turn-9",
        parent_agent_id=None,
        subagent_type="Plan",
        state="running",
        created_at=t1,
        last_activity_at=t1,
    )
    store.upsert(
        agent_id="ag-other-user",
        user_id="user-2",
        conversation_id="conv-a",
        turn_id="turn-9",
        parent_agent_id=None,
        subagent_type="Plan",
        state="running",
        created_at=t1,
        last_activity_at=t1,
    )

    conv_a_u1 = store.list_for_conversation("conv-a", user_id="user-1")
    assert [r.agent_id for r in conv_a_u1] == ["ag-1", "ag-2"]

    conv_b = store.list_for_conversation("conv-b", user_id="user-1")
    assert [r.agent_id for r in conv_b] == ["ag-other-conv"]


def test_agent_run_store_mark_interrupted_on_boot_only_affects_running_and_stalled() -> None:
    engine = _engine()
    t0 = datetime(2026, 11, 21, 15, 0, 0, tzinfo=UTC)
    t_boot = t0 + timedelta(minutes=10)
    store = AgentRunStore(engine, clock=lambda: t_boot)

    states = {
        "ag-running": "running",
        "ag-stalled": "stalled",
        "ag-queued": "queued",
        "ag-completed": "completed",
        "ag-failed": "failed",
        "ag-cancelled": "cancelled",
        "ag-interrupted": "interrupted",
    }
    for agent_id, st in states.items():
        store.upsert(
            agent_id=agent_id,
            user_id="u1",
            conversation_id="conv-1",
            turn_id="turn-1",
            subagent_type="general-purpose",
            state=st,
            created_at=t0,
            last_activity_at=t0,
            finished_at=t0 if st in {"completed", "failed", "cancelled"} else None,
        )

    changed = store.mark_interrupted_on_boot()
    assert changed == 2

    assert store.get("ag-running").state == "interrupted"
    assert store.get("ag-running").finished_at == t_boot
    assert store.get("ag-stalled").state == "interrupted"
    assert store.get("ag-stalled").finished_at == t_boot

    # Terminal and non-running states stay intact.
    assert store.get("ag-completed").state == "completed"
    assert store.get("ag-completed").finished_at == t0
    assert store.get("ag-failed").state == "failed"
    assert store.get("ag-cancelled").state == "cancelled"
    assert store.get("ag-queued").state == "queued"
    assert store.get("ag-interrupted").state == "interrupted"


def test_agent_run_store_rejects_invalid_state() -> None:
    engine = _engine()
    store = AgentRunStore(engine)
    with pytest.raises(ValueError):
        store.upsert(
            agent_id="ag-bad",
            user_id="u1",
            conversation_id="c1",
            turn_id="t1",
            subagent_type="Explore",
            state="bogus",
        )


def test_agent_worktree_store_crud_and_merge_state_updates() -> None:
    engine = _engine()
    t0 = datetime(2026, 11, 21, 16, 0, 0, tzinfo=UTC)
    store = AgentWorktreeStore(engine, clock=lambda: t0)

    rec = store.upsert(
        agent_id="ag-wt-1",
        workspace_root="C:/repo/demo",
        base_commit="abc123def456",
        branch="orin/wt/ag-wt-1",
        merge_state="pending",
        merge_detail=None,
        queued_at=t0,
        merged_at=None,
    )
    assert isinstance(rec, AgentWorktreeRecord)
    assert rec.agent_id == "ag-wt-1"
    assert rec.workspace_root == "C:/repo/demo"
    assert rec.base_commit == "abc123def456"
    assert rec.branch == "orin/wt/ag-wt-1"
    assert rec.merge_state == "pending"
    assert rec.merge_detail is None
    assert rec.queued_at == t0
    assert rec.merged_at is None
    assert store.get("ag-wt-1") == rec
    assert store.get("missing") is None

    t1 = t0 + timedelta(seconds=30)
    conflict = store.update_merge_state(
        "ag-wt-1",
        "conflict",
        merge_detail={"files": ["src/app.py"], "reason": "overlapping hunks"},
    )
    assert conflict is not None
    assert conflict.merge_state == "conflict"
    assert conflict.merge_detail == {"files": ["src/app.py"], "reason": "overlapping hunks"}
    assert conflict.merged_at is None

    applied = store.update_merge_state(
        "ag-wt-1",
        "applied",
        merge_detail={"files": ["src/app.py"]},
        merged_at=t1,
    )
    assert applied is not None
    assert applied.merge_state == "applied"
    assert applied.merged_at == t1

    store.create(
        agent_id="ag-wt-2",
        workspace_root="C:/repo/other",
        base_commit="999",
        branch="orin/wt/ag-wt-2",
        merge_state="empty",
    )

    demo_wts = store.list_for_workspace("C:/repo/demo")
    assert [w.agent_id for w in demo_wts] == ["ag-wt-1"]

    with pytest.raises(ValueError):
        store.update_merge_state("ag-wt-1", "invalid-merge-state")

    assert store.delete("ag-wt-1") is True
    assert store.delete("ag-wt-1") is False
    assert store.get("ag-wt-1") is None
