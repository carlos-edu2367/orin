"""End-to-end verification of the Orin 0.13.0 Alembic chain (0061 <-> 0066)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from agentos.persistence.postgres.migrate import downgrade, upgrade
from agentos.persistence.postgres.schema import (
    agent_runs,
    agent_worktrees,
    memory_events,
    metadata,
    model_context_observations,
    subscription_quota_snapshots,
    task_board_items,
    verification_findings,
    verification_reviews,
)

BASE_012_REVISION = "0061_activity_events_overview_index"
EXPECTED_013_CHAIN = [
    "0062_subscription_quota_snapshots",
    "0063_agent_message_delivery",
    "0064_memory_lifecycle_and_agent_scope",
    "0065_verification_reviews",
    "0066_agent_runs_and_task_board",
    "0067_pipeline_runs",
]


def test_alembic_013_chain_is_strictly_linear_with_single_head() -> None:
    cfg = Config()
    migrations_dir = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "agentos"
        / "persistence"
        / "postgres"
        / "migrations"
    )
    cfg.set_main_option("script_location", str(migrations_dir))
    script = ScriptDirectory.from_config(cfg)

    assert script.get_heads() == [EXPECTED_013_CHAIN[-1]]

    walked = list(
        reversed(
            [
                rev.revision
                for rev in script.iterate_revisions(
                    upper=EXPECTED_013_CHAIN[-1],
                    lower=BASE_012_REVISION,
                )
            ]
        )
    )
    assert walked == EXPECTED_013_CHAIN


def test_full_013_upgrade_downgrade_reupgrade_cycle_from_0061(tmp_path: Path) -> None:
    db_path = tmp_path / "orin release 013 ação.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    real_agent_id = "agent:chat_8b685ddc48644d26a0a34d5e799c05fa:76fbff46f7f5d9aea4048435"
    assert len(real_agent_id) == 68
    now = datetime(2026, 11, 21, 20, 0, 0, tzinfo=UTC)

    try:
        # 1. Start at 0.12.31/0.12.33 baseline (0061) and capture baseline schema snapshot.
        upgrade(engine, BASE_012_REVISION)
        insp_0061 = inspect(engine)
        tables_0061 = set(insp_0061.get_table_names())
        cols_0061 = {
            t: {c["name"] for c in insp_0061.get_columns(t)}
            for t in sorted(tables_0061)
        }

        # Seed realistic 0.12 data (active + superseded + duplicate memories, custom agent).
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO user_agents ("
                    "user_id, name, description, prompt, models_json, disallowed_tools_json, "
                    "enabled, created_at, updated_at"
                    ") VALUES ("
                    "'u1', 'reviewer-local', 'Revisor local', 'Revise com rigor.', '[]', '[]', "
                    "1, :ts, :ts"
                    ")"
                ),
                {"ts": now.isoformat()},
            )
            conn.execute(
                text(
                    "INSERT INTO agent_memories ("
                    "memory_id, user_id, conversation_id, scope_type, scope_id, project_id, "
                    "fact, tags, kind, confidence, emotional_valence, salience, source, "
                    "hit_count, superseded_by, created_at, updated_at"
                    ") VALUES "
                    "('mem_act_1', 'u1', 'c1', 'user', 'u1', NULL, 'Usar uv run pytest.', '[]', 'operational', 1.0, 0.0, 0.5, 'user_explicit', 1, NULL, :ts, :ts),"
                    "('mem_dup_a', 'u1', 'c1', 'user', 'u1', NULL, 'Rodar tsc -b sempre.', '[]', 'preference', 0.8, 0.0, 0.5, 'mechanical', 0, NULL, :ts, :ts),"
                    "('mem_dup_b', 'u1', 'c1', 'user', 'u1', NULL, '  rodar TSC -b sempre! ', '[]', 'preference', 0.8, 0.0, 0.5, 'mechanical', 0, NULL, :ts, :ts),"
                    "('mem_old_1', 'u1', 'c1', 'user', 'u1', NULL, 'Fato antigo.', '[]', 'fact', 0.6, 0.0, 0.5, 'mechanical', 0, 'mem_act_1', :ts, :ts)"
                ),
                {"ts": now.isoformat()},
            )

        # 2. One-shot upgrade from 0061 to head (0066) — exact user upgrade path.
        upgrade(engine, "head")
        assert Path(f"{db_path}.pre-0064.bak").is_file()

        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == EXPECTED_013_CHAIN[-1]

        # Populate every new 0.13 table (including 68-char agent IDs).
        with engine.begin() as conn:
            conn.execute(
                subscription_quota_snapshots.insert().values(
                    user_id="u1",
                    provider="claude-cli",
                    window_kind="five_hour",
                    used_percent=42.5,
                    status="ok",
                    resets_at=now,
                    source="stream",
                    scope_model="claude-sonnet-5-5",
                    observed_at=now,
                    raw={"pct": 42.5},
                )
            )
            conn.execute(
                model_context_observations.insert().values(
                    user_id="u1",
                    provider="claude-cli",
                    model_id="claude-fable-5-2",
                    observed_limit_tokens=200_000,
                    source="error",
                    first_message_tokens=198_500,
                    cli_version="2.1.0",
                    account_credits_on=False,
                    observed_at=now,
                    invalidated_at=None,
                )
            )
            conn.execute(
                memory_events.insert().values(
                    memory_id="mem_act_1",
                    user_id="u1",
                    event="approved",
                    old_text=None,
                    new_text="Usar uv run pytest.",
                    related_id=None,
                    actor="user",
                    conversation_id="c1",
                    turn_id="t1",
                    created_at=now,
                )
            )
            conn.execute(
                verification_reviews.insert().values(
                    review_id="rev_01",
                    user_id="u1",
                    conversation_id="c1",
                    turn_id="t1",
                    agent_id=real_agent_id,
                    round=1,
                    trigger_reason="threshold",
                    files_changed=3,
                    lines_changed=85,
                    author_provider="claude-cli",
                    author_model="claude-haiku-5-5",
                    author_effort="medium",
                    reviewer_provider="codex-cli",
                    reviewer_model="gpt-6-luna",
                    reviewer_effort="high",
                    policy="cross_family",
                    fallback_step=0,
                    verdict="approved",
                    input_tokens=1200,
                    output_tokens=300,
                    cost_usd=None,
                    started_at=now,
                    finished_at=now,
                )
            )
            conn.execute(
                verification_findings.insert().values(
                    review_id="rev_01",
                    finding_id="f_01",
                    severity="low",
                    category="edge_case",
                    has_evidence=True,
                    resolution="fixed",
                    resolution_reason="Coberto por teste.",
                )
            )
            conn.execute(
                agent_runs.insert().values(
                    agent_id=real_agent_id,
                    user_id="u1",
                    conversation_id="c1",
                    turn_id="t1",
                    parent_agent_id=None,
                    subagent_type="antigravity",
                    provider="google-antigravity",
                    model_id="gemini-4-neon-high",
                    effort="high",
                    state="completed",
                    task_digest="abc123",
                    history_path=None,
                    worktree_path=None,
                    pipeline_run_id=None,
                    last_activity_at=now,
                    tool_calls=5,
                    input_tokens=2000,
                    output_tokens=800,
                    cost_usd=None,
                    result_ref=None,
                    created_at=now,
                    finished_at=now,
                )
            )
            conn.execute(
                agent_worktrees.insert().values(
                    agent_id=real_agent_id,
                    workspace_root="C:/tmp/repo",
                    base_commit="deadbeef",
                    branch=f"orin/agent-{real_agent_id}",
                    merge_state="applied",
                    merge_detail={"files": ["a.py"]},
                    queued_at=now,
                    merged_at=now,
                )
            )
            conn.execute(
                task_board_items.insert().values(
                    item_id="item_01",
                    conversation_id="c1",
                    team_name="core",
                    title="Validar cadeia 0061..0066",
                    description="Ciclo completo upgrade/downgrade.",
                    status="completed",
                    owner_agent_id=real_agent_id,
                    blocked_by=[],
                    lease_expires_at=None,
                    result_ref="ok",
                    created_at=now,
                    updated_at=now,
                )
            )

        # 3. Downgrade all the way back to 0061 and verify exact table & column parity with 0061.
        downgrade(engine, BASE_012_REVISION)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == BASE_012_REVISION
            remaining_active = conn.execute(
                text("SELECT COUNT(*) FROM agent_memories WHERE superseded_by IS NULL")
            ).scalar()
            # Two distinct active facts survive ('Usar uv run pytest.' + deduplicated 'Rodar tsc -b sempre.')
            assert remaining_active == 2

        insp_down = inspect(engine)
        tables_down = set(insp_down.get_table_names())
        assert tables_down == tables_0061
        cols_down = {
            t: {c["name"] for c in insp_down.get_columns(t)}
            for t in sorted(tables_down)
        }
        assert cols_down == cols_0061

        # 4. Re-upgrade back to 0066 head and verify exact parity with schema.metadata.
        upgrade(engine, "head")
        insp_reup = inspect(engine)
        assert set(metadata.tables.keys()) <= (set(insp_reup.get_table_names()) - {"alembic_version"})
        for table_name, table in metadata.tables.items():
            assert {c["name"] for c in insp_reup.get_columns(table_name)} == set(table.c.keys())
    finally:
        engine.dispose()
