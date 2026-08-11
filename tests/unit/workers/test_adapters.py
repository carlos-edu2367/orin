from datetime import UTC, datetime, timedelta

from agentos.workers.adapters import RedisArqWorkQueue
from agentos.workers.models import WorkerOperationContext, WorkerPool, WorkItem, WorkKind


class RecordingArq:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def enqueue_job(self, name: str, **kwargs: object) -> str:
        self.calls.append((name, kwargs))
        return "job-1"


def test_arq_adapter_enqueues_only_opaque_work_references() -> None:
    context = WorkerOperationContext("user-1", "workspace-1", "agent-1", "execution-1", "correlation-1", "run", "worker")
    item = WorkItem(
        "item-1", "dispatch-1", "attempt-1", "execution-1", context, WorkerPool.AGENT,
        WorkKind.AGENT_EXECUTION, "payload-1", 1, datetime.now(UTC), datetime.now(UTC) + timedelta(minutes=1),
        1, 2, timedelta(seconds=30), "key-1",
    )
    arq = RecordingArq()
    receipt = RedisArqWorkQueue(arq, namespace="test").enqueue(item)

    assert receipt.job_id == "job-1"
    assert arq.calls[0][0] == "agentos:AGENT"
    assert arq.calls[0][1] == {"work_item_id": "item-1", "dispatch_attempt_id": "attempt-1"}
