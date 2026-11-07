"""Ensure Alembic migrations stay 100% in sync with SQLAlchemy schema.metadata."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect

from agentos.persistence.postgres.migrate import downgrade, upgrade
from agentos.persistence.postgres.schema import metadata


def test_alembic_head_matches_schema_metadata_columns() -> None:
    """Every table and column in schema.metadata must exist after ``upgrade(engine)``."""
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine)
    inspector = inspect(engine)
    migrated_tables = set(inspector.get_table_names()) - {"alembic_version"}

    missing_tables = set(metadata.tables.keys()) - migrated_tables
    assert not missing_tables, (
        f"Tables declared in schema.metadata missing from Alembic migrations: {sorted(missing_tables)}"
    )

    column_drift: dict[str, dict[str, list[str]]] = {}
    for table_name, table in sorted(metadata.tables.items()):
        expected_cols = set(table.c.keys())
        actual_cols = {col["name"] for col in inspector.get_columns(table_name)}
        missing_cols = sorted(expected_cols - actual_cols)
        extra_cols = sorted(actual_cols - expected_cols)
        if missing_cols or extra_cols:
            column_drift[table_name] = {
                "missing_in_migrations": missing_cols,
                "extra_in_migrations": extra_cols,
            }
    assert not column_drift, f"Alembic migration column drift vs schema.metadata: {column_drift}"


def test_migration_0056_adds_and_drops_risk_acknowledged_at() -> None:
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine, "0055_friend_identity")
    cols_0055 = {col["name"] for col in inspect(engine).get_columns("provider_configurations")}
    assert "risk_acknowledged_at" not in cols_0055

    upgrade(engine, "head")
    cols_head = {col["name"] for col in inspect(engine).get_columns("provider_configurations")}
    assert "risk_acknowledged_at" in cols_head

    downgrade(engine, "0055_friend_identity")
    cols_downgraded = {col["name"] for col in inspect(engine).get_columns("provider_configurations")}
    assert "risk_acknowledged_at" not in cols_downgraded
