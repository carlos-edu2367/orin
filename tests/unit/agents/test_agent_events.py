from agentos.agents.models import OpaqueAgentReference
from agentos.agents.compat import InMemoryAgentOutboxSource
from agentos.events import InMemoryEventBus, InMemoryOutboxPublisher, EventContext, PublicationLease, PublishOutboxBatch
from agentos.agents.ports import AssignAgentWorkspace, UnassignAgentWorkspace


def test_confirmed_mutation_creates_minimal_event_with_execution_and_sequence(agent_fixture):
    agent_fixture.confirm_created()
    event = agent_fixture.persistence.confirmed_outbox()[-1]
    assert event.event_type == "AgentCreated"
    assert event.execution_id is not None
    assert event.agent_id == "agent:1"
    assert event.sequence == 1
    assert event.causation_id == "create:1"
    assert event.payload["agent_version"] == 1
    assert "prompt" not in repr(event)


def test_all_lifecycle_mutations_use_minimal_past_tense_events(agent_fixture):
    agent_fixture.confirm_created()
    suspend = agent_fixture.administration.suspend_command(
        agent_id="agent:1", user_id="user:1", workspace_id=None, actor="actor:1",
        expected_version=1, idempotency_key="suspend:event",
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_suspend(suspend))
    resume = agent_fixture.administration.resume_command(
        agent_id="agent:1", user_id="user:1", workspace_id=None, actor="actor:1",
        expected_version=1, idempotency_key="resume:event",
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_resume(resume))
    archive = agent_fixture.administration.archive_command(
        agent_id="agent:1", user_id="user:1", workspace_id=None, actor="actor:1",
        expected_version=1, idempotency_key="archive:event",
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_archive(archive))
    assert [event.event_type for event in agent_fixture.persistence.confirmed_outbox()] == [
        "AgentCreated", "AgentSuspended", "AgentResumed", "AgentArchived"
    ]
    assert all(event.event_id.startswith("event:agent-admin:") for event in agent_fixture.persistence.confirmed_outbox())


def test_agent_does_not_publish_directly_to_event_bus(agent_fixture):
    agent_fixture.confirm_created()
    assert agent_fixture.persistence.confirmed_outbox()
    assert not hasattr(agent_fixture.administration, "publish")


def test_unknown_commit_requires_inspection_and_does_not_duplicate(agent_fixture):
    agent_fixture.persistence.indeterminate_next()
    reference = agent_fixture.administration.request_create(agent_fixture.create_command)
    first = agent_fixture.execution_gate.confirm(reference)
    assert first.receipt.commit_state.value == "UNKNOWN"
    second = agent_fixture.execution_gate.confirm(reference)
    assert second.receipt.transaction_id == first.receipt.transaction_id
    assert len(agent_fixture.persistence.confirmed_outbox()) == 1


def test_configuration_and_workspace_events_contain_only_version_and_reference_codes(agent_fixture, configuration_factory):
    agent_fixture.confirm_created()
    configuration = configuration_factory(config_version=2, supersedes_version=1)
    command = __import__("agentos.agents.ports", fromlist=["ReconfigureAgent"]).ReconfigureAgent(
        actor="actor:1", user_id="user:1", workspace_id=None, agent_id="agent:1",
        correlation_id="correlation:config", idempotency_key="reconfigure:event",
        requested_at=agent_fixture.now, expected_version=1, configuration=configuration,
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_reconfigure(command))
    event = agent_fixture.persistence.confirmed_outbox()[-1]
    assert event.event_type == "AgentConfigurationChanged"
    assert event.payload == {
        "agent_id": "agent:1", "agent_version": 2, "operation_code": "RECONFIGURE"
    }


def test_outbox_publisher_delivers_only_after_agent_commit(agent_fixture):
    agent_fixture.confirm_created()
    bus = InMemoryEventBus()
    publisher = InMemoryOutboxPublisher(InMemoryAgentOutboxSource(agent_fixture.persistence), bus)
    request = PublishOutboxBatch(
        publisher_ref="publisher:agent",
        partition_ref="partition:agents",
        after_position=None,
        maximum_events=10,
        lease=PublicationLease("lease:1", "publisher:1", 1),
    )
    assert bus.events == ()
    result = publisher.publish_pending(request)
    assert result.published_event_ids == ("event:agent-admin:1:1",)
    assert bus.events[0].event_type == "AgentCreated"


def test_unknown_agent_commit_requires_authorized_outbox_inspection(agent_fixture):
    agent_fixture.persistence.indeterminate_next()
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_create(agent_fixture.create_command))
    bus = InMemoryEventBus()
    publisher = InMemoryOutboxPublisher(InMemoryAgentOutboxSource(agent_fixture.persistence), bus)
    request = PublishOutboxBatch(
        publisher_ref="publisher:agent", partition_ref="partition:agents", after_position=None,
        maximum_events=10, lease=PublicationLease("lease:2", "publisher:1", 1),
    )
    pending = publisher.publish_pending(request)
    assert pending.pending_count == 1
    context = EventContext(
        user_id="user:1", workspace_id=None, agent_id="agent:1",
        execution_id="agent-admin:1", correlation_id="correlation:1", purpose="publish",
    )
    published = publisher.publish_pending(__import__("dataclasses").replace(request, context=context))
    assert published.published_event_ids == ("event:agent-admin:1:1",)


def test_workspace_assignment_events_use_target_workspace_ownership(agent_fixture):
    agent_fixture.confirm_created()
    assign = AssignAgentWorkspace(
        actor="actor:1", user_id="user:1", workspace_id=None, agent_id="agent:1",
        correlation_id="correlation:assign-event", idempotency_key="assign:event",
        requested_at=agent_fixture.now, expected_version=1,
        assigned_workspace_id="workspace:1", assignment_ref=agent_fixture.configuration.model_profile_ref,
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_assign_workspace(assign))
    unassign = UnassignAgentWorkspace(
        actor="actor:1", user_id="user:1", workspace_id=None, agent_id="agent:1",
        correlation_id="correlation:unassign-event", idempotency_key="unassign:event",
        requested_at=agent_fixture.now, expected_version=2,
        assigned_workspace_id="workspace:1",
    )
    agent_fixture.execution_gate.confirm(agent_fixture.administration.request_unassign_workspace(unassign))
    events = agent_fixture.persistence.confirmed_outbox()
    assert events[1].event_type == "AgentWorkspaceAssigned"
    assert events[1].workspace_id == "workspace:1"
    assert events[2].event_type == "AgentWorkspaceUnassigned"
    assert events[2].workspace_id == "workspace:1"
