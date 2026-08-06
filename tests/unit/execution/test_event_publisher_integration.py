from agentos.events import EventContext, InMemoryEventBus, PublicationLease, PublishOutboxBatch
from agentos.events.compat import InMemoryTransactionalOutboxSource
from agentos.events.in_memory import InMemoryOutboxPublisher
from agentos.execution.ports import TransitionExecution
from agentos.execution.models import ExecutionState


def test_execution_control_commits_state_and_outbox_before_publisher_reads_it(
    make_control, command_context, now
):
    control, persistence = make_control(ExecutionState.QUEUED)
    command = TransitionExecution(
        context=command_context,
        command_id="command-event-publisher",
        idempotency_key="key-event-publisher",
        expected_version=1,
        requested_at=now,
        target_state=ExecutionState.STARTING,
        reason_code="dispatch",
    )
    accepted = control.transition(command)
    assert accepted.resulting_version == 2

    bus = InMemoryEventBus()
    publisher = InMemoryOutboxPublisher(InMemoryTransactionalOutboxSource(persistence), bus)
    request = PublishOutboxBatch(
        "publisher:1",
        "execution-outbox",
        None,
        10,
        PublicationLease("lease:1", "worker:1", 10),
        EventContext(
            "user-1", "workspace-1", "agent-1", "execution-1", "correlation-1", "publish-events"
        ),
    )
    result = publisher.publish_pending(request)
    assert result.published_event_ids == (persistence.outbox[0].event.event_id,)
    assert persistence.get("execution-1").state is ExecutionState.STARTING
    assert len(persistence.audit_log) == 1
