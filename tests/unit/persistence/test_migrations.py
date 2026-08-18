from datetime import UTC, datetime

from sqlalchemy import MetaData, create_engine, insert, inspect, select, text

from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.provider_secrets import ProviderSecretCipher


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


def test_migrations_enforce_record_classification_values():
    engine = create_engine("sqlite:///:memory:")

    upgrade(engine)

    constraints = inspect(engine).get_check_constraints("persistence_records")

    assert any(item["name"] == "ck_persistence_records_classification" for item in constraints)


def test_migration_0038_preserves_a_credential_saved_before_encryption_existed(monkeypatch) -> None:
    """A provider_configurations row that never went through the old lazy
    upgrade-on-read (workers/chat.py's since-removed _credential_value) still
    holds its key in plaintext, with api_key_ciphertext NULL. Migration 0038
    drops both of those columns; if it only backfilled rows that already had
    ciphertext, this credential would be silently destroyed on upgrade."""
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "0" * 32)
    engine = create_engine("sqlite:///:memory:")
    upgrade(engine, "0037_plugin_commands_and_hooks")

    schema = MetaData()
    schema.reflect(bind=engine)
    provider_configurations = schema.tables["provider_configurations"]
    now = datetime(2020, 1, 1, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(insert(provider_configurations).values(
            user_id="legacy-user", provider="openrouter", enabled=True, model=None,
            api_key="plain-legacy-secret", api_key_ciphertext=None,
            base_url=None, secret_ref="legacy-ref", catalog_refreshed_at=None,
            created_at=now, updated_at=now,
        ))

    upgrade(engine)

    schema = MetaData()
    schema.reflect(bind=engine)
    provider_api_keys = schema.tables["provider_api_keys"]
    with engine.connect() as connection:
        row = connection.execute(select(provider_api_keys).where(provider_api_keys.c.user_id == "legacy-user")).mappings().first()
    assert row is not None
    assert row["position"] == 0
    cipher = ProviderSecretCipher.from_environment(required=True)
    assert cipher.decrypt(row["api_key_ciphertext"]) == "plain-legacy-secret"
