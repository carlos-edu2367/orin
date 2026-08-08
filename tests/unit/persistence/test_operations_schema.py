from agentos.persistence.postgres.schema import dispatch_attempts, dispatches, schedule_occurrences, schedules


def test_operation_schema_exposes_durable_dispatch_and_schedule_tables() -> None:
    assert dispatches.name == "worker_dispatches"
    assert dispatch_attempts.name == "worker_dispatch_attempts"
    assert schedule_occurrences.name == "schedule_occurrences"
    assert schedules.name == "schedules"
    assert {"dispatch_id", "idempotency_key", "version"} <= set(dispatches.c.keys())
    assert {"occurrence_id", "state_fencing_token", "execution_id"} <= set(schedule_occurrences.c.keys())
