from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from agentos.persistence.postgres.schema import metadata
from agentos.workers.models import WorkerOperationContext, WorkerPool, WorkItem, WorkKind
from agentos.workers.postgres import PostgresDispatchStore


def test_postgres_dispatch_store_uses_compare_and_set_for_a_lease() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    now = datetime.now(UTC)
    item = WorkItem("item-1", "dispatch-1", "attempt-1", "execution-1", WorkerOperationContext("user-1", None, "agent-1", "execution-1", "correlation-1", "run", "worker"), WorkerPool.AGENT, WorkKind.AGENT_EXECUTION, "payload-1", 1, now, now + timedelta(minutes=1), 1, 2, timedelta(seconds=30), "key-1")
    store = PostgresDispatchStore(engine)
    store.create(item)

    leased = store.lease("attempt-1", worker_id="worker-1", lease_id="lease-1", fence=1, expected_version=1, expires_at=now + timedelta(seconds=30))
    assert leased.version == 2
    assert store.acknowledge("attempt-1", lease_id="lease-1", fence=1, expected_version=2).state.value == "ACKNOWLEDGED"
