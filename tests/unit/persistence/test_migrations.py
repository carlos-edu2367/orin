from sqlalchemy import create_engine, inspect

from agentos.persistence.postgres.migrate import upgrade


def test_migrations_are_explicit_and_do_not_run_on_engine_construction():
    engine = create_engine("sqlite:///:memory:")

    assert inspect(engine).get_table_names() == []

    upgrade(engine)

    assert set(inspect(engine).get_table_names()) >= {
        "persistence_records",
        "persistence_audit",
        "persistence_outbox",
        "persistence_idempotency",
        "alembic_version",
    }
