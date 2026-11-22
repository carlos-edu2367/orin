"""Add memory lifecycle columns, events table, partial active hash index, and user_agents.memory (WS-D2)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from agentos.memory.models_write import compute_text_hash
from agentos.persistence.postgres.migrate import backup_before_migration

revision = "0064_memory_lifecycle_and_agent_scope"
down_revision = "0063_agent_message_delivery"
branch_labels = None
depends_on = None

MEMORIES_TABLE = "agent_memories"
EVENTS_TABLE = "memory_events"
USER_AGENTS_TABLE = "user_agents"
BATCH_SIZE = 500


def _table_names(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _column_names(bind, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _index_names(bind, table: str) -> set[str]:
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "")
    if dialect == "sqlite":
        rows = bind.execute(
            sa.text(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = :tbl AND name IS NOT NULL"
            ),
            {"tbl": table},
        ).scalars().all()
        return {str(name) for name in rows}
    if dialect == "postgresql":
        rows = bind.execute(
            sa.text("SELECT indexname FROM pg_indexes WHERE tablename = :tbl"),
            {"tbl": table},
        ).scalars().all()
        return {str(name) for name in rows}
    return {ix["name"] for ix in sa.inspect(bind).get_indexes(table) if ix.get("name")}


def _unique_constraint_names(bind, table: str) -> set[str]:
    return {uq["name"] for uq in sa.inspect(bind).get_unique_constraints(table) if uq.get("name")}


def _backfill_text_hashes_in_batches(bind) -> None:
    """Compute blake2b text_hash in batches of 500 for rows that do not have it yet."""
    last_id = ""
    while True:
        rows = bind.execute(
            sa.text(
                "SELECT memory_id, fact FROM agent_memories "
                "WHERE (text_hash IS NULL OR text_hash = '') AND memory_id > :last_id "
                "ORDER BY memory_id ASC LIMIT :limit"
            ),
            {"last_id": last_id, "limit": BATCH_SIZE},
        ).mappings().all()
        if not rows:
            break
        updates = [
            {"mid": str(r["memory_id"]), "th": compute_text_hash(str(r["fact"] or ""))}
            for r in rows
        ]
        bind.execute(
            sa.text("UPDATE agent_memories SET text_hash = :th WHERE memory_id = :mid"),
            updates,
        )
        last_id = str(rows[-1]["memory_id"])
        if len(rows) < BATCH_SIZE:
            break


def _deduplicate_active_hashes(bind) -> None:
    """Collapse pre-existing active duplicates with identical scope+text_hash.

    Keeps the most recent row active (superseded_by IS NULL AND invalid_at IS NULL) and marks
    older duplicates as ``status = 'superseded'`` pointing to the active winner with ``invalid_at``
    populated, preserving the strict invariant ``status == 'active' <=> superseded_by IS NULL AND invalid_at IS NULL``.
    """
    dup_groups = bind.execute(
        sa.text(
            "SELECT user_id, scope_type, scope_id, "
            "COALESCE(project_id, '') AS proj_key, "
            "COALESCE(agent_key, '') AS agent_k, "
            "text_hash "
            "FROM agent_memories "
            "WHERE status = 'active' "
            "GROUP BY user_id, scope_type, scope_id, COALESCE(project_id, ''), COALESCE(agent_key, ''), text_hash "
            "HAVING COUNT(*) > 1"
        )
    ).mappings().all()

    for grp in dup_groups:
        members = bind.execute(
            sa.text(
                "SELECT memory_id, updated_at, created_at FROM agent_memories "
                "WHERE status = 'active' "
                "  AND user_id = :uid "
                "  AND scope_type = :stype "
                "  AND scope_id = :sid "
                "  AND COALESCE(project_id, '') = :proj_key "
                "  AND COALESCE(agent_key, '') = :agent_k "
                "  AND text_hash = :th "
                "ORDER BY updated_at ASC, created_at ASC, memory_id ASC"
            ),
            {
                "uid": grp["user_id"],
                "stype": grp["scope_type"],
                "sid": grp["scope_id"],
                "proj_key": grp["proj_key"],
                "agent_k": grp["agent_k"],
                "th": grp["text_hash"],
            },
        ).mappings().all()
        if len(members) <= 1:
            continue
        winner_id = str(members[-1]["memory_id"])
        losers = [
            {
                "mid": str(m["memory_id"]),
                "winner": winner_id,
                "inv": m["updated_at"] or m["created_at"],
            }
            for m in members[:-1]
        ]
        bind.execute(
            sa.text(
                "UPDATE agent_memories "
                "SET status = 'superseded', superseded_by = :winner, invalid_at = COALESCE(invalid_at, :inv) "
                "WHERE memory_id = :mid"
            ),
            losers,
        )


def upgrade() -> None:
    bind = op.get_bind()
    backup_before_migration(bind, tag="pre-0064")

    tables = _table_names(bind)

    # 1. Add user_agents.memory VARCHAR(16) NULL
    if USER_AGENTS_TABLE in tables:
        ua_cols = _column_names(bind, USER_AGENTS_TABLE)
        if "memory" not in ua_cols:
            op.add_column(USER_AGENTS_TABLE, sa.Column("memory", sa.String(length=16), nullable=True))

    # 2. Add the 8 new columns to agent_memories with safe server_defaults
    if MEMORIES_TABLE in tables:
        mem_cols = _column_names(bind, MEMORIES_TABLE)
        columns_to_add = [
            ("status", sa.Column("status", sa.String(length=16), nullable=False, server_default="active")),
            ("agent_key", sa.Column("agent_key", sa.String(length=64), nullable=True)),
            ("text_hash", sa.Column("text_hash", sa.String(length=64), nullable=False, server_default="")),
            ("valid_from", sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True)),
            ("invalid_at", sa.Column("invalid_at", sa.DateTime(timezone=True), nullable=True)),
            ("source_kind", sa.Column("source_kind", sa.String(length=16), nullable=False, server_default="agent")),
            ("source_ref", sa.Column("source_ref", sa.String(length=512), nullable=True)),
            ("trust", sa.Column("trust", sa.SmallInteger(), nullable=False, server_default=sa.text("1"))),
        ]
        for col_name, col_def in columns_to_add:
            if col_name not in mem_cols:
                op.add_column(MEMORIES_TABLE, col_def)

        # 3. SQL backfill for status, valid_from, invalid_at, source_kind, trust, source_ref
        bind.execute(
            sa.text("UPDATE agent_memories SET valid_from = created_at WHERE valid_from IS NULL")
        )
        bind.execute(
            sa.text(
                "UPDATE agent_memories "
                "SET status = 'superseded', invalid_at = COALESCE(invalid_at, updated_at) "
                "WHERE superseded_by IS NOT NULL AND (status = 'active' OR invalid_at IS NULL)"
            )
        )
        bind.execute(
            sa.text(
                "UPDATE agent_memories SET invalid_at = NULL "
                "WHERE status = 'active' AND invalid_at IS NOT NULL"
            )
        )
        bind.execute(
            sa.text(
                "UPDATE agent_memories SET source_kind = 'user', trust = 2 "
                "WHERE source = 'user_explicit' AND source_kind = 'agent'"
            )
        )
        bind.execute(
            sa.text(
                "UPDATE agent_memories SET source_kind = 'consolidator', trust = 1 "
                "WHERE source = 'consolidation' AND source_kind = 'agent'"
            )
        )
        bind.execute(
            sa.text(
                "UPDATE agent_memories "
                "SET source_ref = COALESCE(source_message_id, source_execution_id) "
                "WHERE source_ref IS NULL AND (source_message_id IS NOT NULL OR source_execution_id IS NOT NULL)"
            )
        )

        # 4. Backfill text_hash in batches of 500 and deduplicate active hashes
        _backfill_text_hashes_in_batches(bind)
        _deduplicate_active_hashes(bind)

        # 5. Drop legacy uq_agent_memories_scope_fact using batch_alter_table(recreate="always")
        uqs = _unique_constraint_names(bind, MEMORIES_TABLE)
        if "uq_agent_memories_scope_fact" in uqs:
            with op.batch_alter_table(MEMORIES_TABLE, recreate="always") as batch_op:
                batch_op.drop_constraint("uq_agent_memories_scope_fact", type_="unique")

        # 6. Create partial unique index and scope+status index
        existing_indexes = _index_names(bind, MEMORIES_TABLE)
        if "uq_agent_memories_active_hash" not in existing_indexes:
            op.create_index(
                "uq_agent_memories_active_hash",
                MEMORIES_TABLE,
                [
                    "user_id",
                    "scope_type",
                    "scope_id",
                    sa.text("COALESCE(project_id, '')"),
                    sa.text("COALESCE(agent_key, '')"),
                    "text_hash",
                ],
                unique=True,
                sqlite_where=sa.text("status = 'active'"),
                postgresql_where=sa.text("status = 'active'"),
            )
        if "ix_agent_memories_scope_status" not in existing_indexes:
            op.create_index(
                "ix_agent_memories_scope_status",
                MEMORIES_TABLE,
                ["user_id", "scope_type", "project_id", "agent_key", "status"],
            )
        # Ensure existing indexes survived batch recreate
        if "ix_agent_memories_user_updated" not in existing_indexes:
            op.create_index("ix_agent_memories_user_updated", MEMORIES_TABLE, ["user_id", "updated_at"])
        if "ix_agent_memories_scope" not in existing_indexes:
            op.create_index(
                "ix_agent_memories_scope",
                MEMORIES_TABLE,
                ["user_id", "scope_type", "project_id", "updated_at"],
            )

    # 7. Create memory_events table and its index
    if EVENTS_TABLE not in tables:
        op.create_table(
            EVENTS_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("memory_id", sa.String(length=255), nullable=False),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("event", sa.String(length=24), nullable=False),
            sa.Column("old_text", sa.String(length=4000), nullable=True),
            sa.Column("new_text", sa.String(length=4000), nullable=True),
            sa.Column("related_id", sa.String(length=255), nullable=True),
            sa.Column("actor", sa.String(length=80), nullable=False),
            sa.Column("conversation_id", sa.String(length=255), nullable=True),
            sa.Column("turn_id", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    event_indexes = _index_names(bind, EVENTS_TABLE)
    if "ix_memory_events_memory_created" not in event_indexes:
        op.create_index("ix_memory_events_memory_created", EVENTS_TABLE, ["memory_id", "created_at"])


def _deduplicate_for_downgrade(bind) -> None:
    """Deduplicate (user_id, scope_type, project_id, fact) keeping active then newest row before legacy UNIQUE."""
    has_status = "status" in _column_names(bind, MEMORIES_TABLE)
    order_sql = (
        "ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END ASC, "
        "updated_at DESC, created_at DESC, memory_id DESC"
        if has_status
        else "ORDER BY updated_at DESC, created_at DESC, memory_id DESC"
    )
    rows = bind.execute(
        sa.text(
            "SELECT memory_id, user_id, scope_type, project_id, fact, updated_at, created_at "
            f"FROM agent_memories {order_sql}"
        )
    ).mappings().all()
    seen: set[tuple[str, str, str | None, str]] = set()
    to_delete: list[dict[str, str]] = []
    for row in rows:
        key = (
            str(row["user_id"]),
            str(row["scope_type"]),
            None if row["project_id"] is None else str(row["project_id"]),
            str(row["fact"]),
        )
        if key in seen:
            to_delete.append({"mid": str(row["memory_id"])})
        else:
            seen.add(key)
    for start in range(0, len(to_delete), BATCH_SIZE):
        bind.execute(
            sa.text("DELETE FROM agent_memories WHERE memory_id = :mid"),
            to_delete[start : start + BATCH_SIZE],
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)

    # 1. Drop memory_events
    if EVENTS_TABLE in tables:
        event_indexes = _index_names(bind, EVENTS_TABLE)
        if "ix_memory_events_memory_created" in event_indexes:
            op.drop_index("ix_memory_events_memory_created", table_name=EVENTS_TABLE)
        op.drop_table(EVENTS_TABLE)

    # 2. Drop new indexes, deduplicate, drop new columns, recreate legacy unique constraint
    if MEMORIES_TABLE in tables:
        existing_indexes = _index_names(bind, MEMORIES_TABLE)
        if "uq_agent_memories_active_hash" in existing_indexes:
            op.drop_index("uq_agent_memories_active_hash", table_name=MEMORIES_TABLE)
        if "ix_agent_memories_scope_status" in existing_indexes:
            op.drop_index("ix_agent_memories_scope_status", table_name=MEMORIES_TABLE)

        mem_cols = _column_names(bind, MEMORIES_TABLE)
        uqs = _unique_constraint_names(bind, MEMORIES_TABLE)
        cols_to_drop = [
            col
            for col in (
                "trust",
                "source_ref",
                "source_kind",
                "invalid_at",
                "valid_from",
                "text_hash",
                "agent_key",
                "status",
            )
            if col in mem_cols
        ]
        need_uq = "uq_agent_memories_scope_fact" not in uqs

        if "status" in mem_cols:
            bind.execute(
                sa.text(
                    "DELETE FROM agent_memories "
                    "WHERE status IN ('deleted', 'quarantined', 'invalid', 'proposed') "
                    "   OR scope_type = 'agent'"
                )
            )

        if need_uq:
            _deduplicate_for_downgrade(bind)

        if cols_to_drop or need_uq:
            with op.batch_alter_table(MEMORIES_TABLE, recreate="always") as batch_op:
                for col in cols_to_drop:
                    batch_op.drop_column(col)
                if need_uq:
                    batch_op.create_unique_constraint(
                        "uq_agent_memories_scope_fact",
                        ["user_id", "scope_type", "project_id", "fact"],
                    )

        after_indexes = _index_names(bind, MEMORIES_TABLE)
        if "ix_agent_memories_user_updated" not in after_indexes:
            op.create_index("ix_agent_memories_user_updated", MEMORIES_TABLE, ["user_id", "updated_at"])
        if "ix_agent_memories_scope" not in after_indexes:
            op.create_index(
                "ix_agent_memories_scope",
                MEMORIES_TABLE,
                ["user_id", "scope_type", "project_id", "updated_at"],
            )

    # 3. Drop user_agents.memory
    if USER_AGENTS_TABLE in tables:
        ua_cols = _column_names(bind, USER_AGENTS_TABLE)
        if "memory" in ua_cols:
            with op.batch_alter_table(USER_AGENTS_TABLE) as batch_op:
                batch_op.drop_column("memory")
