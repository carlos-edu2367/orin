from agentos.persistence.postgres.schema import metadata


def test_persistence_schema_contains_only_the_durable_boundary_tables():
    assert set(metadata.tables) == {
        "persistence_records",
        "persistence_audit",
        "persistence_outbox",
        "persistence_idempotency",
        "persistence_clock",
    }


def test_schema_has_ownership_versions_and_integrity_constraints():
    records = metadata.tables["persistence_records"]
    outbox = metadata.tables["persistence_outbox"]
    idempotency = metadata.tables["persistence_idempotency"]

    for column in ("record_ref", "record_type", "version", "user_id", "workspace_id", "agent_id", "execution_id", "classification"):
        assert column in records.c
    assert any(constraint.name == "uq_persistence_records_record_ref" for constraint in records.constraints)
    assert any(constraint.name == "uq_persistence_outbox_event_id" for constraint in outbox.constraints)
    assert any(constraint.name == "uq_persistence_idempotency_scope" for constraint in idempotency.constraints)
    assert idempotency.c.workspace_scope.nullable is False
    assert idempotency.c.correlation_id.nullable is False
    assert idempotency.c.records.nullable is False
    assert any(constraint.name == "ck_persistence_idempotency_revision_nonnegative" for constraint in idempotency.constraints)


def test_schema_binds_audit_and_outbox_to_the_full_record_scope():
    records = metadata.tables["persistence_records"]
    audit = metadata.tables["persistence_audit"]
    outbox = metadata.tables["persistence_outbox"]

    assert "workspace_scope" in records.c
    assert "workspace_scope" in audit.c
    assert "workspace_scope" in outbox.c
    assert any(constraint.name == "uq_persistence_records_ownership" for constraint in records.constraints)
    assert any(len(constraint.column_keys) == 8 for constraint in audit.foreign_key_constraints)
    assert any(len(constraint.column_keys) == 8 for constraint in outbox.foreign_key_constraints)
