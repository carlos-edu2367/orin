"""Tests for Alembic migration 0064_memory_lifecycle_and_agent_scope (WS-D2)."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from agentos.memory.models_write import compute_text_hash
from agentos.persistence.postgres.migrate import backup_before_migration, downgrade, upgrade
from agentos.persistence.postgres.schema import agent_memories, memory_events, user_agents

PREVIOUS_REVISION = "0063_agent_message_delivery"
TARGET_REVISION = "0064_memory_lifecycle_and_agent_scope"
NEW_MEMORY_COLS = {
    "status",
    "agent_key",
    "text_hash",
    "valid_from",
    "invalid_at",
    "source_kind",
    "source_ref",
    "trust",
}


def _seed_pre_0064_database(engine) -> dict[str, list[str]]:
    """Seed 1,200 memories + 40 superseded + 10 user_explicit + 5 active duplicates (NULL project_id)."""
    base = datetime(2026, 10, 1, 10, 0, 0, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    superseded_ids: list[str] = []
    user_explicit_ids: list[str] = []
    dup_ids: list[str] = []

    # 1. 1,145 regular active memories (mechanical + consolidation mix)
    for i in range(1145):
        ts = base + timedelta(minutes=i)
        src = "consolidation" if i % 5 == 0 else "mechanical"
        rows.append(
            {
                "memory_id": f"mem_bulk_{i:04d}",
                "user_id": "u1",
                "conversation_id": "conv-1",
                "scope_type": "user",
                "scope_id": "u1",
                "project_id": None,
                "source_message_id": None,
                "source_execution_id": f"exec_{i:04d}" if i % 3 == 0 else None,
                "fact": f"Fato historico numero {i} sobre o repositorio local.",
                "tags": "[]",
                "kind": "fact",
                "confidence": 0.8,
                "emotional_valence": 0.0,
                "salience": 0.5,
                "source": src,
                "hit_count": 0,
                "last_used_at": None,
                "superseded_by": None,
                "created_at": ts.isoformat(),
                "updated_at": ts.isoformat(),
            }
        )

    # 2. 10 user_explicit active memories
    for i in range(10):
        ts = base + timedelta(hours=30, minutes=i)
        mid = f"mem_user_{i:02d}"
        user_explicit_ids.append(mid)
        rows.append(
            {
                "memory_id": mid,
                "user_id": "u1",
                "conversation_id": "conv-user",
                "scope_type": "user",
                "scope_id": "u1",
                "project_id": None,
                "source_message_id": f"msg_{i:02d}",
                "source_execution_id": None,
                "fact": f"Preferencia explicita numero {i} do usuario.",
                "tags": "[]",
                "kind": "preference",
                "confidence": 1.0,
                "emotional_valence": 0.0,
                "salience": 0.5,
                "source": "user_explicit",
                "hit_count": 2,
                "last_used_at": ts.isoformat(),
                "superseded_by": None,
                "created_at": ts.isoformat(),
                "updated_at": ts.isoformat(),
            }
        )

    # 3. 40 superseded memories pointing to active successors
    for i in range(40):
        ts_old = base + timedelta(hours=40, minutes=i)
        ts_upd = ts_old + timedelta(minutes=15)
        old_id = f"mem_sup_{i:02d}"
        superseded_ids.append(old_id)
        rows.append(
            {
                "memory_id": old_id,
                "user_id": "u1",
                "conversation_id": "conv-sup",
                "scope_type": "user",
                "scope_id": "u1",
                "project_id": None,
                "source_message_id": None,
                "source_execution_id": f"exec_sup_{i:02d}",
                "fact": f"Fato antigo superado numero {i}.",
                "tags": "[]",
                "kind": "operational",
                "confidence": 0.7,
                "emotional_valence": 0.0,
                "salience": 0.5,
                "source": "mechanical",
                "hit_count": 0,
                "last_used_at": None,
                "superseded_by": f"mem_bulk_{i:04d}",
                "created_at": ts_old.isoformat(),
                "updated_at": ts_upd.isoformat(),
            }
        )

    # 4. 5 active duplicates with NULL project_id (old UNIQUE didn't prevent NULL duplicates in SQLite)
    # Different casing/punctuation/spacing that normalize to the exact same text_hash.
    dup_facts = [
        "O projeto usa pnpm.",
        "o projeto usa pnpm",
        "  O   projeto usa pnpm!  ",
        "O projeto usa pnpm...",
        "o PROJETO usa pnpm;",
    ]
    for i, text_val in enumerate(dup_facts):
        ts = base + timedelta(hours=50, minutes=i * 5)
        mid = f"mem_dup_{i:02d}"
        dup_ids.append(mid)
        rows.append(
            {
                "memory_id": mid,
                "user_id": "u1",
                "conversation_id": "conv-dup",
                "scope_type": "user",
                "scope_id": "u1",
                "project_id": None,
                "source_message_id": None,
                "source_execution_id": None,
                "fact": text_val,
                "tags": "[]",
                "kind": "operational",
                "confidence": 0.8,
                "emotional_valence": 0.0,
                "salience": 0.5,
                "source": "mechanical",
                "hit_count": 0,
                "last_used_at": None,
                "superseded_by": None,
                "created_at": ts.isoformat(),
                "updated_at": ts.isoformat(),
            }
        )

    assert len(rows) == 1200

    insert_sql = text(
        "INSERT INTO agent_memories ("
        "memory_id, user_id, conversation_id, scope_type, scope_id, project_id, "
        "source_message_id, source_execution_id, fact, tags, kind, confidence, "
        "emotional_valence, salience, source, hit_count, last_used_at, superseded_by, "
        "created_at, updated_at"
        ") VALUES ("
        ":memory_id, :user_id, :conversation_id, :scope_type, :scope_id, :project_id, "
        ":source_message_id, :source_execution_id, :fact, :tags, :kind, :confidence, "
        ":emotional_valence, :salience, :source, :hit_count, :last_used_at, :superseded_by, "
        ":created_at, :updated_at"
        ")"
    )
    with engine.begin() as conn:
        conn.execute(insert_sql, rows)

    return {
        "superseded": superseded_ids,
        "user_explicit": user_explicit_ids,
        "duplicates": dup_ids,
    }


def test_backup_before_migration_creates_sqlite_backup_without_overwriting(tmp_path: Path) -> None:
    db_path = tmp_path / "banco ação 0064.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE sample (id INTEGER PRIMARY KEY, val TEXT)"))
            conn.execute(text("INSERT INTO sample VALUES (1, 'v1')"))

        bak = backup_before_migration(engine, tag="pre-0064")
        assert bak == Path(f"{db_path}.pre-0064.bak")
        assert bak.is_file()

        # Mutate live DB and run backup_before_migration again: existing backup must NOT be overwritten.
        with engine.begin() as conn:
            conn.execute(text("UPDATE sample SET val = 'v2' WHERE id = 1"))
        bak2 = backup_before_migration(engine, tag="pre-0064")
        assert bak2 == bak

        bak_engine = create_engine(f"sqlite:///{bak.as_posix()}")
        try:
            with bak_engine.connect() as conn:
                assert conn.execute(text("SELECT val FROM sample WHERE id = 1")).scalar() == "v1"
        finally:
            bak_engine.dispose()

        # In-memory SQLite returns None cleanly.
        mem_engine = create_engine("sqlite:///:memory:")
        try:
            assert backup_before_migration(mem_engine, tag="pre-0064") is None
        finally:
            mem_engine.dispose()
    finally:
        engine.dispose()


def test_migration_0064_backfills_1200_rows_creates_backup_is_idempotent_and_downgrades(tmp_path: Path) -> None:
    db_path = tmp_path / "orin mig 0064 ação.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        upgrade(engine, PREVIOUS_REVISION)
        inspector = inspect(engine)
        assert NEW_MEMORY_COLS.isdisjoint({c["name"] for c in inspector.get_columns("agent_memories")})
        assert "memory_events" not in set(inspector.get_table_names())
        assert "memory" not in {c["name"] for c in inspector.get_columns("user_agents")}

        seeded = _seed_pre_0064_database(engine)

        # Upgrade to 0064 (and run twice to assert idempotency)
        upgrade(engine, TARGET_REVISION)
        upgrade(engine, TARGET_REVISION)

        # Backup file must exist alongside the SQLite database.
        backup_file = Path(f"{db_path}.pre-0064.bak")
        assert backup_file.is_file()

        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == TARGET_REVISION

        # Schema sync check (user_agents.isolation is added later in 0066, access_mode in 0068)
        inspector = inspect(engine)
        assert {c["name"] for c in inspector.get_columns("agent_memories")} == {c.name for c in agent_memories.columns}
        assert {c["name"] for c in inspector.get_columns("memory_events")} == {c.name for c in memory_events.columns}
        assert {c["name"] for c in inspector.get_columns("user_agents")} == (
            {c.name for c in user_agents.columns} - {"isolation", "access_mode"}
        )

        with engine.connect() as conn:
            sqlite_indexes = {
                str(name)
                for name in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'agent_memories'")
                ).scalars()
            }
        assert "uq_agent_memories_active_hash" in sqlite_indexes
        assert "ix_agent_memories_scope_status" in sqlite_indexes
        uqs = {uq["name"] for uq in inspector.get_unique_constraints("agent_memories")}
        assert "uq_agent_memories_scope_fact" not in uqs

        event_indexes = {ix["name"] for ix in inspector.get_indexes("memory_events")}
        assert "ix_memory_events_memory_created" in event_indexes

        # Verify data integrity on all 1,200 rows
        with engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM agent_memories")).scalar()
            assert total == 1200

            # Strict invariant: status == 'active' <=> superseded_by IS NULL AND invalid_at IS NULL
            bad_active = conn.execute(
                text(
                    "SELECT COUNT(*) FROM agent_memories "
                    "WHERE status = 'active' AND (superseded_by IS NOT NULL OR invalid_at IS NOT NULL)"
                )
            ).scalar()
            bad_inactive = conn.execute(
                text(
                    "SELECT COUNT(*) FROM agent_memories "
                    "WHERE status <> 'active' AND superseded_by IS NULL AND invalid_at IS NULL"
                )
            ).scalar()
            assert bad_active == 0
            assert bad_inactive == 0

            # Every row got a valid 64-char blake2b text_hash and valid_from
            empty_hashes = conn.execute(
                text("SELECT COUNT(*) FROM agent_memories WHERE text_hash IS NULL OR LENGTH(text_hash) <> 64")
            ).scalar()
            missing_valid_from = conn.execute(
                text("SELECT COUNT(*) FROM agent_memories WHERE valid_from IS NULL")
            ).scalar()
            assert empty_hashes == 0
            assert missing_valid_from == 0

            # 10 user_explicit rows mapped to source_kind='user', trust=2, source_ref=msg_XX
            user_rows = conn.execute(
                text(
                    "SELECT memory_id, source_kind, trust, source_ref FROM agent_memories "
                    "WHERE memory_id LIKE 'mem_user_%'"
                )
            ).mappings().all()
            assert len(user_rows) == 10
            assert all(r["source_kind"] == "user" and r["trust"] == 2 for r in user_rows)
            assert all(str(r["source_ref"]).startswith("msg_") for r in user_rows)

            # Consolidation rows mapped to source_kind='consolidator', trust=1
            cons_rows = conn.execute(
                text("SELECT source_kind, trust FROM agent_memories WHERE source = 'consolidation'")
            ).mappings().all()
            assert cons_rows
            assert all(r["source_kind"] == "consolidator" and r["trust"] == 1 for r in cons_rows)

            # 40 previously superseded rows are status='superseded' with invalid_at populated
            sup_rows = conn.execute(
                text(
                    "SELECT status, superseded_by, invalid_at FROM agent_memories "
                    "WHERE memory_id LIKE 'mem_sup_%'"
                )
            ).mappings().all()
            assert len(sup_rows) == 40
            assert all(r["status"] == "superseded" and r["superseded_by"] and r["invalid_at"] for r in sup_rows)

            # 5 duplicates collapsed so exactly 1 is active and 4 are superseded pointing to the active one
            dup_rows = conn.execute(
                text(
                    "SELECT memory_id, status, superseded_by, invalid_at, text_hash FROM agent_memories "
                    "WHERE memory_id LIKE 'mem_dup_%' ORDER BY memory_id"
                )
            ).mappings().all()
            assert len(dup_rows) == 5
            expected_hash = compute_text_hash("O projeto usa pnpm.")
            assert all(r["text_hash"] == expected_hash for r in dup_rows)
            active_dups = [r for r in dup_rows if r["status"] == "active"]
            sup_dups = [r for r in dup_rows if r["status"] == "superseded"]
            assert len(active_dups) == 1
            assert len(sup_dups) == 4
            winner_id = active_dups[0]["memory_id"]
            assert all(r["superseded_by"] == winner_id and r["invalid_at"] is not None for r in sup_dups)

        # Direct upgrade() re-invocation when already upgraded must be a no-op
        mig = importlib.import_module(
            "agentos.persistence.postgres.migrations.versions.0064_memory_lifecycle_and_agent_scope"
        )
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mig.upgrade()

        # Downgrade cleanly restores uq_agent_memories_scope_fact and drops new columns/table
        downgrade(engine, PREVIOUS_REVISION)
        downgrade(engine, PREVIOUS_REVISION)

        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == PREVIOUS_REVISION
        inspector = inspect(engine)
        assert "memory_events" not in set(inspector.get_table_names())
        assert NEW_MEMORY_COLS.isdisjoint({c["name"] for c in inspector.get_columns("agent_memories")})
        assert "memory" not in {c["name"] for c in inspector.get_columns("user_agents")}
        assert "uq_agent_memories_scope_fact" in {
            uq["name"] for uq in inspector.get_unique_constraints("agent_memories")
        }

        # Direct downgrade() re-invocation when already downgraded must be a no-op
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mig.downgrade()

        # Re-upgrade cleanly after downgrade
        upgrade(engine, TARGET_REVISION)
        assert "memory_events" in set(inspect(engine).get_table_names())
        assert len(seeded["duplicates"]) == 5
    finally:
        engine.dispose()
