"""Tests for Alembic migration 0065_verification_reviews (WS-A6)."""

from __future__ import annotations

import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from agentos.persistence.postgres.migrate import downgrade, upgrade
from agentos.persistence.postgres.schema import verification_findings, verification_reviews

PREVIOUS_REVISION = "0064_memory_lifecycle_and_agent_scope"
TARGET_REVISION = "0065_verification_reviews"


def test_migration_0065_upgrades_creates_tables_columns_and_indexes() -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine, PREVIOUS_REVISION)

    pre_tables = set(inspect(engine).get_table_names())
    assert "verification_reviews" not in pre_tables
    assert "verification_findings" not in pre_tables

    upgrade(engine, TARGET_REVISION)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "verification_reviews" in tables
    assert "verification_findings" in tables

    review_cols = {col["name"] for col in inspector.get_columns("verification_reviews")}
    assert review_cols == set(verification_reviews.c.keys())

    finding_cols = {col["name"] for col in inspector.get_columns("verification_findings")}
    assert finding_cols == set(verification_findings.c.keys())

    review_pk = inspector.get_pk_constraint("verification_reviews")["constrained_columns"]
    assert review_pk == ["review_id"]

    finding_pk = inspector.get_pk_constraint("verification_findings")["constrained_columns"]
    assert finding_pk == ["review_id", "finding_id"]

    review_indexes = {ix["name"] for ix in inspector.get_indexes("verification_reviews")}
    assert "ix_verification_reviews_user_started" in review_indexes
    assert "ix_verification_reviews_conversation_turn" in review_indexes

    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == TARGET_REVISION


def test_migration_0065_downgrades_and_reupgrades_cleanly() -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine, TARGET_REVISION)

    downgrade(engine, PREVIOUS_REVISION)
    tables_after_down = set(inspect(engine).get_table_names())
    assert "verification_reviews" not in tables_after_down
    assert "verification_findings" not in tables_after_down
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == PREVIOUS_REVISION

    upgrade(engine, TARGET_REVISION)
    tables_after_reup = set(inspect(engine).get_table_names())
    assert "verification_reviews" in tables_after_reup
    assert "verification_findings" in tables_after_reup
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == TARGET_REVISION


def test_migration_0065_upgrade_and_downgrade_are_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine, TARGET_REVISION)

    migration = importlib.import_module(
        "agentos.persistence.postgres.migrations.versions.0065_verification_reviews"
    )

    # Re-running upgrade() on an already upgraded DB must be a safe no-op.
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()

    assert {"verification_reviews", "verification_findings"} <= set(inspect(engine).get_table_names())

    # Running downgrade() twice must also be idempotent.
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.downgrade()
            migration.downgrade()

    tables = set(inspect(engine).get_table_names())
    assert "verification_reviews" not in tables
    assert "verification_findings" not in tables
