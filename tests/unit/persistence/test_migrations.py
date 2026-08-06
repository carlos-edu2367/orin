from sqlalchemy import create_engine, inspect, text

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


def test_migration_initializes_revision_clock_from_existing_receipts():
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine, "0001_initial_persistence")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO persistence_idempotency "
                "(user_id, workspace_id, agent_id, execution_id, purpose, actor, "
                "idempotency_key, fingerprint, transaction_id, commit_state, receipt, "
                "store_revision, created_at) VALUES "
                "('user:1', NULL, 'agent:1', 'execution:1', 'execution.persist', 'actor:1', "
                "'key:1', 'fingerprint:1', 'transaction:1', 'COMMITTED', '{}', 7, CURRENT_TIMESTAMP)"
            )
        )

    upgrade(engine)

    with engine.connect() as connection:
        assert connection.execute(text("SELECT revision FROM persistence_clock")).scalar_one() == 7
        assert connection.execute(text("SELECT correlation_id FROM persistence_idempotency")).scalar_one().startswith("__legacy__:")
